from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models import PlatformAccount, User
from backend.app.platform import build_registry
from backend.app.services import realtime as realtime_module
from backend.app.services.events import EventBus
from backend.app.services.realtime import RealtimeManager
from backend.app.services.runners import RunnerManager


class FakeMirror:
    instances: list["FakeMirror"] = []

    def __init__(self, settings, db, adapters, bus) -> None:  # noqa: ANN001
        self.status = {"state": "stopped", "detail": "", "owners": []}
        self._task = None
        self.started_with: list[str] = []
        FakeMirror.instances.append(self)

    async def start(self, owner_id: str) -> None:
        self.started_with.append(owner_id)
        self.status = {"state": "running", "detail": owner_id, "owners": [owner_id]}

    async def stop(self) -> None:
        self.status = {"state": "stopped", "detail": "", "owners": []}


class FakeDws:
    instances: list["FakeDws"] = []

    def __init__(self, settings, db, adapters, bus) -> None:  # noqa: ANN001
        self.status = {"state": "stopped", "detail": "", "events": 0}
        self.started_with: list[str] = []
        FakeDws.instances.append(self)

    async def start(self, owner_id: str) -> None:
        self.started_with.append(owner_id)
        self.status = {"state": "running", "detail": "", "events": 0}

    async def stop(self) -> None:
        self.status = {"state": "stopped", "detail": "", "events": 0}


class FakePoller:
    instances: list["FakePoller"] = []

    def __init__(self, settings, db, adapters, bus) -> None:  # noqa: ANN001
        self.status = {"state": "stopped", "detail": "", "polls": 0}
        self.started = 0
        FakePoller.instances.append(self)

    async def start(self) -> None:
        self.started += 1
        self.status = {"state": "running", "detail": "", "polls": 0}

    async def stop(self) -> None:
        self.status = {"state": "stopped", "detail": "", "polls": 0}


class FakeFeishuRunner:
    instances: list["FakeFeishuRunner"] = []

    def __init__(self, settings, db, adapters, bus, owner_id) -> None:  # noqa: ANN001
        self.platform = "feishu"
        self.owner_id = owner_id
        self.status = {"state": "running", "detail": ""}
        FakeFeishuRunner.instances.append(self)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.status = {"state": "stopped", "detail": ""}


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'multi.db').as_posix()}",
        "data_dir": tmp_path,
        "file_dir": tmp_path / "files",
        "frontend_dist": tmp_path / "dist",
        "secret_key": "test-secret-key-for-multi-user-checks",
        "demo_mode": False,
        "enable_real_channels": True,
        "multi_user_channels": True,
        "feishu_user_mirror_enabled": True,
        "dingtalk_dws_im_mirror_enabled": True,
        "dingtalk_dws_enabled": True,
        "wecom_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


async def _seed_accounts(db: Database) -> dict[str, str]:
    """Three users: two with platform accounts, one without."""
    async with db.session() as session:
        users = {}
        for key, platform, handle in (
            ("alice", "feishu", "ou_alice"),
            ("alice_dt", "dingtalk", "alice_dt"),
            ("bob", "feishu", "ou_bob_multi"),
            ("carol", None, None),
        ):
            name = key.split("_")[0]
            if name not in users:
                user = User(email=f"{name}@example.com", display_name=name.title())
                session.add(user)
                await session.flush()
                users[name] = user.id
            if platform:
                session.add(
                    PlatformAccount(
                        user_id=users[name],
                        platform=platform,
                        platform_user_id=handle,
                        display_name=name.title(),
                        status="connected",
                    )
                )
        await session.commit()
        return users


@pytest.fixture()
def patches(monkeypatch: pytest.MonkeyPatch):
    FakeMirror.instances.clear()
    FakeDws.instances.clear()
    FakePoller.instances.clear()
    FakeFeishuRunner.instances.clear()
    monkeypatch.setattr(realtime_module, "FeishuUserMirror", FakeMirror)
    monkeypatch.setattr(realtime_module, "DingTalkDwsMirror", FakeMirror)
    monkeypatch.setattr(realtime_module, "DwsSidecar", FakeDws)
    monkeypatch.setattr(realtime_module, "WeComTodoPoller", FakePoller)
    monkeypatch.setattr("backend.app.services.runners.FeishuRunner", FakeFeishuRunner)
    yield


@pytest.mark.asyncio
async def test_multi_user_channels_start_per_owner(tmp_path: Path, patches):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    users = await _seed_accounts(db)
    adapters = build_registry(settings)
    bus = EventBus()
    manager = RealtimeManager(settings, db, adapters, bus)

    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 2, "alice has both Feishu and DingTalk accounts"
    assert FakeDws.instances[0].started_with == [users["alice"]]
    assert FakePoller.instances[0].started == 1

    await manager.start(users["bob"])
    assert len(FakeMirror.instances) == 3, "bob adds one Feishu mirror"
    assert FakeDws.instances[0].started_with == [users["alice"]], "dws CLI binds one owner"

    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 3, "starting the same owner twice is a no-op"

    status = manager.status()
    assert status["settings"]["multi_user"] is True
    assert set(status["owners"]) == {users["alice"], users["bob"]}
    assert status["owners"][users["alice"]]["feishu_mirror"]["state"] == "running"
    # The dws login profile belongs to the first owner, so bob gets no DingTalk
    # mirror (his Feishu mirror still runs independently).
    assert status["owners"][users["bob"]]["dingtalk_mirror"]["state"] == "idle"
    assert status["settings"]["dws_owner"] == users["alice"]

    await manager.stop()
    assert all(mirror.status["state"] == "stopped" for mirror in FakeMirror.instances)
    assert manager.status()["owners"] == {}
    await db.dispose()


@pytest.mark.asyncio
async def test_owner_without_platform_account_gets_no_mirror(tmp_path: Path, patches):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    users = await _seed_accounts(db)
    manager = RealtimeManager(settings, db, build_registry(settings), EventBus())

    await manager.start(users["carol"])
    assert FakeMirror.instances == []
    assert manager.status()["owners"][users["carol"]] == {
        "feishu_mirror": {"state": "idle", "detail": "未启用或该账号未绑定平台"},
        "dingtalk_mirror": {"state": "idle", "detail": "未启用或该账号未绑定平台"},
    }
    await manager.stop()
    await db.dispose()


@pytest.mark.asyncio
async def test_single_operator_mode_is_unchanged(tmp_path: Path, patches):
    settings = _settings(tmp_path, multi_user_channels=False)
    db = Database(settings)
    await db.init()
    users = await _seed_accounts(db)
    manager = RealtimeManager(settings, db, build_registry(settings), EventBus())

    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 2
    await manager.start(users["bob"])
    assert len(FakeMirror.instances) == 2, "single-operator mode ignores a second owner"

    status = manager.status()
    assert status["settings"]["multi_user"] is False
    assert "owners" not in status
    await manager.stop()
    await db.dispose()


@pytest.mark.asyncio
async def test_rebinding_adds_the_missing_channel(tmp_path: Path, patches):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    users = await _seed_accounts(db)
    manager = RealtimeManager(settings, db, build_registry(settings), EventBus())

    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 2
    await manager.stop()

    # Unbind the Feishu identity, restart: only the DingTalk mirror comes up.
    async with db.session() as session:
        row = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == users["alice"],
                PlatformAccount.platform == "feishu",
            )
        )
        assert row is not None
        await session.delete(row)
        await session.commit()
    FakeMirror.instances.clear()
    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 1

    # Re-bind a Feishu identity: the next start() adds just that channel.
    async with db.session() as session:
        session.add(
            PlatformAccount(
                user_id=users["alice"],
                platform="feishu",
                platform_user_id="ou_alice_rebound",
                display_name="Alice",
                status="connected",
            )
        )
        await session.commit()
    await manager.start(users["alice"])
    assert len(FakeMirror.instances) == 2, "the Feishu mirror is added back once"

    await manager.stop()
    await db.dispose()


@pytest.mark.asyncio
async def test_runner_manager_binds_the_first_bot_owner(tmp_path: Path, patches):
    settings = _settings(
        tmp_path,
        multi_user_channels=False,
        feishu_enabled=True,
        dingtalk_enabled=False,
        wecom_enabled=False,
    )
    db = Database(settings)
    await db.init()
    users = await _seed_accounts(db)
    manager = RunnerManager(settings, db, build_registry(settings), EventBus())

    await manager.start(users["alice"])
    await manager.start(users["bob"])
    assert len(FakeFeishuRunner.instances) == 1
    assert manager.info()["bot_owner_id"] == users["alice"]

    await manager.stop()
    assert manager.info()["bot_owner_id"] is None
    await db.dispose()
