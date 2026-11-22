from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import Database
from ..models import PlatformAccount, Task, TaskLink, User
from ..platform.base import PlatformError
from ..platform.registry import AdapterRegistry
from .accounts import context_for_user
from .events import EventBus
from .feishu_mirror import FeishuUserMirror
from .dingtalk_mirror import DingTalkDwsMirror
from .ingest import TaskEventData, ingest_task_event
from .resilience import platform_gate
from .sync import from_platform_status

logger = logging.getLogger(__name__)


def _status_from_stage(stage: Any) -> str:
    try:
        value = int(stage)
    except (TypeError, ValueError):
        return "open"
    if value == 1:
        return "in_progress"
    if value in (2, 3):
        return "done"
    return "open"


class DwsSidecar:
    """Runs the official ``dws`` CLI as a per-user personal Stream consumer.

    The CLI is intentionally treated as an external adapter: it owns the login
    state and the long connection, while Relay only consumes its NDJSON events.
    """

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._owner_id: str | None = None
        self._cooldown_until = 0.0
        self._last_error = ""
        self.status: dict[str, Any] = {"state": "stopped", "detail": "", "events": 0}

    async def start(self, owner_id: str) -> None:
        if not self.settings.dingtalk_dws_enabled:
            self.status = {"state": "disabled", "detail": "DINGTALK_DWS_ENABLED=false", "events": 0}
            return
        self._owner_id = owner_id
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="dingtalk-dws")

    async def stop(self) -> None:
        self._running = False
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.status = {**self.status, "state": "stopped"}

    def _command(self) -> list[str]:
        args = [
            self.settings.dingtalk_dws_command,
            "event",
            "consume",
            "user_todo_task_create",
            "user_todo_task_update",
            "user_todo_task_delete",
            "--role-types",
            self.settings.dingtalk_dws_role_types,
            "--flatten",
            "--format",
            "ndjson",
        ]
        return shlex.split(" ".join(args), posix=False) if " " in self.settings.dingtalk_dws_command else args

    async def _run_forever(self) -> None:
        backoff = 3
        while self._running:
            try:
                command = self._command()
                self.status = {"state": "starting", "detail": " ".join(command), "events": self.status.get("events", 0)}
                env = os.environ.copy()
                if self.settings.dingtalk_dws_config_dir:
                    env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
                if self.settings.dingtalk_dws_disable_keychain:
                    env["DWS_DISABLE_KEYCHAIN"] = "1"
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                self.status = {
                    "state": "running",
                    "detail": "dws 长连接已启动",
                    "events": self.status.get("events", 0),
                }
                assert self._process.stdout is not None
                assert self._process.stderr is not None
                stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))
                async for raw in self._process.stdout:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    await self._handle_line(line)
                await self._process.wait()
                try:
                    await asyncio.wait_for(stderr_task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    stderr_task.cancel()
                if self._running:
                    cooldown = max(0.0, self._cooldown_until - time.monotonic())
                    backoff = max(backoff, cooldown)
                    self._mark_reconnecting(self._process.returncode, backoff)
            except FileNotFoundError:
                self.status = {
                    "state": "error",
                    "detail": f"找不到 dws 命令：{self.settings.dingtalk_dws_command}",
                    "events": self.status.get("events", 0),
                }
                await asyncio.sleep(30)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("DWS sidecar failed")
                self.status = {"state": "error", "detail": str(exc), "events": self.status.get("events", 0)}
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(max(backoff * 2, 30), 300)

    def _mark_reconnecting(self, returncode: int | None, backoff: float) -> None:
        """Publish a reconnect state that says exactly when the next try is due.

        Operators cannot infer the wait from "1851s 后重连" once it is written;
        the absolute timestamp makes the cooldown checkable without guessing.
        """
        retry_at = datetime.now(UTC) + timedelta(seconds=backoff)
        status: dict[str, Any] = {
            "state": "reconnecting",
            "detail": (
                f"dws 退出码 {returncode}，{int(backoff)}s 后重连"
                f"（{retry_at.astimezone().strftime('%H:%M:%S')}）"
            ),
            "events": self.status.get("events", 0),
            "next_retry_at": retry_at.isoformat(),
            "last_exit_code": returncode,
        }
        if self._last_error:
            status["last_error"] = self._last_error
        self.status = status

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            async for raw in stream:
                text = raw.decode("utf-8", "ignore").strip()
                if not text:
                    continue
                logger.info("dws: %s", text[:500])
                match = re.search(r'"retry_after_seconds"\s*:\s*(\d+)', text)
                if match:
                    try:
                        self._cooldown_until = time.monotonic() + int(match.group(1)) + 3
                    except ValueError:
                        pass
                # The CLI may hand back an absolute deadline instead of a delta;
                # honouring it exactly avoids re-triggering a refreshed cooldown.
                retry_at_match = re.search(r'"next_retry_at"\s*:\s*"([^"]+)"', text)
                if retry_at_match:
                    try:
                        target = datetime.fromisoformat(retry_at_match.group(1).replace("Z", "+00:00"))
                        remaining = max(0.0, (target - datetime.now(UTC)).total_seconds())
                        self._cooldown_until = time.monotonic() + remaining + 3
                    except ValueError:
                        pass
                # Keep the server's own wording. Without it the operator only
                # sees "reconnecting" and cannot tell a transient cooldown from
                # a permanent "this org has no personal-event subscription".
                error_msg = re.search(r'"errorMsg"\s*:\s*"([^"]{0,200})"', text)
                if error_msg:
                    self._last_error = error_msg.group(1)
                if "cooldown" in text or "error" in text:
                    logger.warning("dws stderr: %s", text[:1200])
        except Exception:  # noqa: BLE001
            pass

    async def _handle_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except ValueError:
            logger.debug("Ignoring non-JSON dws line: %s", line[:200])
            return
        if payload.get("type") not in {"user_todo_task_create", "user_todo_task_update", "user_todo_task_delete"}:
            if payload.get("error") or payload.get("success") is False:
                error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
                retry_after = error.get("retry_after_seconds") or payload.get("retry_after_seconds")
                if retry_after:
                    try:
                        self._cooldown_until = time.monotonic() + int(retry_after) + 3
                    except (TypeError, ValueError):
                        pass
                message = error.get("errorMsg") or error.get("message") or payload.get("errorMsg")
                if message:
                    self._last_error = str(message)[:200]
                logger.warning("dws event error: %s", line[:1200])
            return
        task_id = str(payload.get("task_id") or payload.get("taskId") or "")
        if not task_id:
            return
        status = _status_from_stage(payload.get("status_stage"))
        if payload.get("type") == "user_todo_task_delete":
            status = "done"
        occurred_at = None
        timestamp = payload.get("timestamp") or payload.get("update_time")
        if timestamp:
            try:
                occurred_at = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
            except (TypeError, ValueError, OSError):
                occurred_at = None
        assert self._owner_id is not None
        async with self.db.session() as session:
            await ingest_task_event(
                session,
                settings=self.settings,
                adapters=self.adapters,
                bus=self.bus,
                owner_id=self._owner_id,
                data=TaskEventData(
                    platform="dingtalk",
                    event_id=str(payload.get("event_id") or f"dws-{task_id}-{payload.get('update_time')}"),
                    platform_task_id=task_id,
                    status=status,
                    event_type=str(payload.get("type")),
                    title=str(payload.get("subject") or ""),
                    occurred_at=occurred_at,
                    raw=payload,
                ),
            )
        self.status = {**self.status, "events": self.status.get("events", 0) + 1, "detail": f"最近事件：{task_id}"}


class WeComTodoPoller:
    """WeCom has no todo events, so poll linked todos for status drift."""

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.status: dict[str, Any] = {"state": "stopped", "detail": "", "polls": 0}

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="wecom-todo-poller")
        self.status = {"state": "running", "detail": "每 20 秒轮询已关联企微待办", "polls": 0}

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.status = {**self.status, "state": "stopped"}

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("WeCom poller failed")
            await asyncio.sleep(20)

    async def _poll_once(self) -> None:
        async with self.db.session() as session:
            links = (
                await session.scalars(
                    select(TaskLink).where(
                        TaskLink.platform == "wecom",
                        TaskLink.link_state == "active",
                    )
                )
            ).all()
            for link in links:
                task = await session.get(Task, link.task_id)
                if task is None:
                    continue
                try:
                    account = await context_for_user(session, task.owner_id, "wecom", self.settings)
                    adapter = self.adapters.get("wecom")
                    state = await platform_gate.idempotent(
                        "wecom", lambda: adapter.fetch_task(account, link)
                    )
                except PlatformError:
                    continue
                unified = from_platform_status("wecom", state.raw_status)
                if unified == link.platform_status:
                    continue
                await ingest_task_event(
                    session,
                    settings=self.settings,
                    adapters=self.adapters,
                    bus=self.bus,
                    owner_id=task.owner_id,
                    data=TaskEventData(
                        platform="wecom",
                        event_id=f"poll-{link.id}-{unified}-{int(datetime.now(UTC).timestamp())}",
                        platform_task_id=link.platform_task_id,
                        status=unified,
                        event_type="todo.poll",
                        title=state.title or task.title,
                        occurred_at=state.updated_at,
                        raw={"source": "poll"},
                    ),
                )
        self.status = {**self.status, "polls": self.status.get("polls", 0) + 1}


class RealtimeManager:
    """Owns user-token channels (Feishu mirror, DingTalk DWS mirror, WeCom poller).

    Default single-operator mode behaves exactly as before: one owner, one set
    of mirrors. ``MULTI_USER_CHANNELS=true`` adds a mirror set per connected
    owner. Bot/app channels stay single-owner and are handled by RunnerManager;
    the dws CLI also has a single login profile, so only the first owner binds
    it.
    """

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self.dws = DwsSidecar(settings, db, adapters, bus)
        self.wecom = WeComTodoPoller(settings, db, adapters, bus)
        # Single-operator mirrors are created on start; multi-user mode keeps a
        # dict of mirrors per owner instead.
        self.feishu_mirror: FeishuUserMirror | None = None
        self.dingtalk_im: DingTalkDwsMirror | None = None
        self._started = False
        self._owner_id: str | None = None
        self._owners: dict[str, dict[str, Any]] = {}
        self._dws_owner: str | None = None
        self._supervisor: asyncio.Task[None] | None = None

    async def _has_account(self, owner_id: str, platform: str) -> bool:
        async with self.db.session() as session:
            row = await session.scalar(
                select(PlatformAccount.id)
                .where(
                    PlatformAccount.user_id == owner_id,
                    PlatformAccount.platform == platform,
                    PlatformAccount.status == "connected",
                )
                .limit(1)
            )
            return row is not None

    async def start(self, owner_id: str | None) -> None:
        if self.settings.demo_mode or not self.settings.enable_real_channels:
            return
        if self.settings.multi_user_channels:
            await self._start_owner(owner_id)
            return
        if self._started:
            return
        logger.info(
            "Realtime start owner=%s feishu_mirror=%s dingtalk_mirror=%s dws=%s",
            owner_id,
            self.settings.feishu_user_mirror_enabled,
            self.settings.dingtalk_dws_im_mirror_enabled,
            self.settings.dingtalk_dws_enabled,
        )
        self._started = True
        self._owner_id = owner_id
        if owner_id and self.settings.dingtalk_dws_enabled:
            self._dws_owner = owner_id
            await self.dws.start(owner_id)
        if owner_id and self.settings.feishu_user_mirror_enabled:
            self.feishu_mirror = FeishuUserMirror(self.settings, self.db, self.adapters, self.bus)
            await self.feishu_mirror.start(owner_id)
        if owner_id and self.settings.dingtalk_dws_im_mirror_enabled:
            self.dingtalk_im = DingTalkDwsMirror(self.settings, self.db, self.adapters, self.bus)
            await self.dingtalk_im.start(owner_id)
        if self.settings.wecom_enabled:
            await self.wecom.start()
        self._supervisor = asyncio.create_task(self._supervise(), name="realtime-supervisor")

    async def _start_owner(self, owner_id: str | None) -> None:
        if not owner_id:
            return
        channels = dict(self._owners.get(owner_id, {}))
        if (
            self.settings.feishu_user_mirror_enabled
            and "feishu_mirror" not in channels
            and await self._has_account(owner_id, "feishu")
        ):
            mirror = FeishuUserMirror(self.settings, self.db, self.adapters, self.bus)
            await mirror.start(owner_id)
            channels["feishu_mirror"] = mirror
        # The dws CLI has a single login profile, so the DingTalk mirror can only
        # follow the account that owns it (the first one that needed it).
        dingtalk_owner = next(
            (oid for oid, existing in self._owners.items() if "dingtalk_mirror" in existing),
            None,
        )
        if (
            self.settings.dingtalk_dws_im_mirror_enabled
            and "dingtalk_mirror" not in channels
            and dingtalk_owner in (None, owner_id)
            and await self._has_account(owner_id, "dingtalk")
        ):
            mirror = DingTalkDwsMirror(self.settings, self.db, self.adapters, self.bus)
            await mirror.start(owner_id)
            channels["dingtalk_mirror"] = mirror
        self._owners[owner_id] = channels
        if not self._started:
            self._started = True
            if self.settings.dingtalk_dws_enabled:
                # One dws login profile per host: bind it to the first owner.
                self._dws_owner = owner_id
                await self.dws.start(owner_id)
            if self.settings.wecom_enabled:
                await self.wecom.start()
            self._supervisor = asyncio.create_task(self._supervise(), name="realtime-supervisor")
        logger.info("Realtime owner=%s started (%d owners)", owner_id, len(self._owners))

    async def _supervise(self) -> None:
        while self._started:
            await asyncio.sleep(8)
            if not self._started:
                return
            try:
                if self.settings.multi_user_channels:
                    await self._supervise_owners()
                else:
                    await self._supervise_single()
            except Exception:  # noqa: BLE001
                logger.debug("realtime supervisor check failed", exc_info=True)

    async def _supervise_single(self) -> None:
        if not self._owner_id:
            return
        feishu = self.feishu_mirror
        if (
            self.settings.feishu_user_mirror_enabled
            and feishu is not None
            and feishu.status.get("state") == "stopped"
            and (feishu._task is None or feishu._task.done())
        ):
            logger.warning("Restarting Feishu user mirror after stopped state")
            await feishu.start(self._owner_id)
        dingtalk = self.dingtalk_im
        if (
            self.settings.dingtalk_dws_im_mirror_enabled
            and dingtalk is not None
            and dingtalk.status.get("state") == "stopped"
            and (dingtalk._task is None or dingtalk._task.done())
        ):
            logger.warning("Restarting DingTalk DWS IM mirror after stopped state")
            await dingtalk.start(self._owner_id)

    async def _supervise_owners(self) -> None:
        for owner_id, channels in list(self._owners.items()):
            for name, enabled in (
                ("feishu_mirror", self.settings.feishu_user_mirror_enabled),
                ("dingtalk_mirror", self.settings.dingtalk_dws_im_mirror_enabled),
            ):
                mirror = channels.get(name)
                if not enabled or mirror is None:
                    continue
                if mirror.status.get("state") == "stopped" and (
                    mirror._task is None or mirror._task.done()
                ):
                    logger.warning("Restarting %s for owner %s", name, owner_id)
                    await mirror.start(owner_id)

    async def stop(self) -> None:
        self._started = False
        if self._supervisor:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
            self._supervisor = None
        if self.settings.multi_user_channels:
            for channels in self._owners.values():
                for mirror in channels.values():
                    try:
                        await mirror.stop()
                    except Exception:  # noqa: BLE001
                        logger.debug("failed to stop owner mirror", exc_info=True)
            self._owners.clear()
        else:
            if self.feishu_mirror is not None:
                await self.feishu_mirror.stop()
            if self.dingtalk_im is not None:
                await self.dingtalk_im.stop()
        await self.dws.stop()
        await self.wecom.stop()

    def status(self) -> dict[str, Any]:
        if self.settings.multi_user_channels:
            owners: dict[str, dict[str, Any]] = {}
            running = {"feishu_mirror": 0, "dingtalk_mirror": 0}
            for owner_id, channels in self._owners.items():
                entry: dict[str, Any] = {}
                for name in ("feishu_mirror", "dingtalk_mirror"):
                    mirror = channels.get(name)
                    if mirror is None:
                        entry[name] = {"state": "idle", "detail": "未启用或该账号未绑定平台"}
                    else:
                        entry[name] = mirror.status
                        if mirror.status.get("state") == "running":
                            running[name] += 1
                owners[owner_id] = entry
            total = len(self._owners)
            return {
                "dws": self.dws.status,
                "wecom_poller": self.wecom.status,
                "feishu_mirror": {
                    "state": "running" if running["feishu_mirror"] else "stopped",
                    "detail": f"{total} 个账号，{running['feishu_mirror']} 个飞书镜像运行中",
                },
                "dingtalk_mirror": {
                    "state": "running" if running["dingtalk_mirror"] else "stopped",
                    "detail": f"{total} 个账号，{running['dingtalk_mirror']} 个钉钉镜像运行中",
                },
                "owners": owners,
                "settings": {
                    "feishu_mirror": self.settings.feishu_user_mirror_enabled,
                    "dingtalk_mirror": self.settings.dingtalk_dws_im_mirror_enabled,
                    "multi_user": True,
                    "started": self._started,
                    "dws_owner": self._dws_owner,
                },
            }
        return {
            "dws": self.dws.status,
            "wecom_poller": self.wecom.status,
            "feishu_mirror": self.feishu_mirror.status
            if self.feishu_mirror is not None
            else {"state": "stopped", "detail": ""},
            "dingtalk_mirror": self.dingtalk_im.status
            if self.dingtalk_im is not None
            else {"state": "stopped", "detail": ""},
            "settings": {
                "feishu_mirror": self.settings.feishu_user_mirror_enabled,
                "dingtalk_mirror": self.settings.dingtalk_dws_im_mirror_enabled,
                "multi_user": False,
                "started": self._started,
            },
        }


async def find_connected_owners(db: Database) -> list[str]:
    """Users with at least one connected platform account, oldest first."""
    async with db.session() as session:
        rows = (
            await session.scalars(
                select(PlatformAccount.user_id)
                .where(PlatformAccount.status == "connected")
                .order_by(PlatformAccount.created_at)
                .distinct()
            )
        ).all()
        return [str(row) for row in rows]


async def find_owner_with_dingtalk(db: Database) -> str | None:
    async with db.session() as session:
        row = await session.scalar(
            select(PlatformAccount.user_id)
            .where(PlatformAccount.platform == "dingtalk")
            .limit(1)
        )
        return str(row) if row else None


async def find_any_owner(db: Database) -> str | None:
    async with db.session() as session:
        row = await session.scalar(select(User.id).where(User.is_placeholder.is_(False)).limit(1))
        return str(row) if row else None
