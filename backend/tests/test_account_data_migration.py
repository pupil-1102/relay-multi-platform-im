from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db import Database, utcnow
from backend.app.models import (
    ConversationRead,
    FileRecord,
    FileView,
    Message,
    PlatformAccount,
    Task,
    User,
)
from backend.app.services.account_data import (
    MigrationError,
    migrate_owner_data,
    move_platform_binding,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'migrate.db').as_posix()}",
        data_dir=tmp_path,
        file_dir=tmp_path / "files",
        frontend_dist=tmp_path / "dist",
        secret_key="test-secret-key-for-migration-checks",
        demo_mode=True,
    )


async def _seed(db: Database):
    now = utcnow()
    async with db.session() as session:
        alice = User(email="alice@example.com", display_name="Alice")
        bob = User(email="bob@example.com", display_name="Bob")
        session.add_all([alice, bob])
        await session.flush()

        moved = Message(
            owner_id=alice.id,
            platform="feishu",
            direction="inbound",
            platform_msg_id="msg-move",
            platform_chat_id="chat-1",
            text="move me",
            created_at=now,
        )
        conflict = Message(
            owner_id=alice.id,
            platform="feishu",
            direction="inbound",
            platform_msg_id="msg-dup",
            platform_chat_id="chat-1",
            text="conflict",
            created_at=now,
        )
        dingtalk = Message(
            owner_id=alice.id,
            platform="dingtalk",
            direction="inbound",
            platform_msg_id="msg-dt",
            platform_chat_id="chat-dt",
            text="stay for now",
            created_at=now,
        )
        bob_copy = Message(
            owner_id=bob.id,
            platform="feishu",
            direction="inbound",
            platform_msg_id="msg-dup",
            platform_chat_id="chat-9",
            text="bob already has this id",
            created_at=now,
        )
        session.add_all([moved, conflict, dingtalk, bob_copy])
        await session.flush()

        moved_file = FileRecord(
            owner_id=alice.id, message_id=moved.id, platform="feishu", filename="move.pdf"
        )
        conflict_file = FileRecord(
            owner_id=alice.id, message_id=conflict.id, platform="feishu", filename="conflict.pdf"
        )
        asset = FileRecord(
            owner_id=alice.id, message_id=None, platform="asset", filename="price.csv"
        )
        session.add_all([moved_file, conflict_file, asset])
        await session.flush()

        task = Task(
            owner_id=alice.id,
            title="Alice task",
            source_message_id=moved.id,
            source_platform="feishu",
            assignee_id=alice.id,
        )
        session.add(task)

        session.add_all(
            [
                FileView(
                    user_id=alice.id,
                    file_id=moved_file.id,
                    first_opened_at=now - timedelta(hours=2),
                    last_opened_at=now - timedelta(hours=1),
                    open_count=2,
                ),
                FileView(
                    user_id=bob.id,
                    file_id=moved_file.id,
                    first_opened_at=now - timedelta(hours=3),
                    last_opened_at=now - timedelta(minutes=5),
                    open_count=5,
                ),
                ConversationRead(
                    owner_id=alice.id,
                    platform="feishu",
                    platform_chat_id="chat-1",
                    last_read_at=now - timedelta(hours=1),
                ),
                ConversationRead(
                    owner_id=bob.id,
                    platform="feishu",
                    platform_chat_id="chat-1",
                    last_read_at=now - timedelta(minutes=1),
                ),
            ]
        )
        await session.commit()
        return {
            "alice": alice.id,
            "bob": bob.id,
            "moved": moved.id,
            "conflict": conflict.id,
            "dingtalk": dingtalk.id,
            "moved_file": moved_file.id,
            "conflict_file": conflict_file.id,
            "asset": asset.id,
        }


async def _owner(session, model, row_id: str) -> str:
    row = await session.get(model, row_id)
    assert row is not None
    return row.owner_id


@pytest.mark.asyncio
async def test_migration_dry_run_does_not_change_data(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed(db)

    async with db.session() as session:
        report = await migrate_owner_data(
            session, source_id=ids["alice"], target_id=ids["bob"]
        )

    assert report["applied"] is False
    assert report["messages_moved"] == 2, "move + dingtalk message"
    assert report["messages_skipped"] == 1
    assert report["message_conflicts"] == ["msg-dup"]
    assert report["tasks_moved"] == 1
    assert report["files_moved"] == 2, "moved file + asset, not the conflict file"
    assert report["file_views_merged"] == 1
    assert report["reads_merged"] == 1

    async with db.session() as session:
        assert await _owner(session, Message, ids["moved"]) == ids["alice"]
        assert await _owner(session, FileRecord, ids["moved_file"]) == ids["alice"]
    await db.dispose()


@pytest.mark.asyncio
async def test_migration_runs_and_merges_state(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed(db)

    async with db.session() as session:
        report = await migrate_owner_data(
            session, source_id=ids["alice"], target_id=ids["bob"], apply=True
        )
    assert report["applied"] is True

    async with db.session() as session:
        assert await _owner(session, Message, ids["moved"]) == ids["bob"]
        assert await _owner(session, Message, ids["dingtalk"]) == ids["bob"]
        assert await _owner(session, Message, ids["conflict"]) == ids["alice"], "conflict stays"
        assert await _owner(session, FileRecord, ids["moved_file"]) == ids["bob"]
        assert await _owner(session, FileRecord, ids["asset"]) == ids["bob"]
        assert await _owner(session, FileRecord, ids["conflict_file"]) == ids["alice"]

        task = await session.scalar(select(Task).where(Task.title == "Alice task"))
        assert task is not None
        assert task.owner_id == ids["bob"]
        assert task.assignee_id == ids["bob"]

        views = list(
            (
                await session.scalars(
                    select(FileView).where(FileView.file_id == ids["moved_file"])
                )
            ).all()
        )
        assert len(views) == 1, "duplicate view merged into one"
        assert views[0].user_id == ids["bob"]
        assert views[0].open_count == 5

        reads = list(
            (
                await session.scalars(
                    select(ConversationRead).where(
                        ConversationRead.platform_chat_id == "chat-1"
                    )
                )
            ).all()
        )
        assert len(reads) == 1
        assert reads[0].owner_id == ids["bob"]
    await db.dispose()


@pytest.mark.asyncio
async def test_migration_can_be_limited_to_one_platform(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed(db)

    async with db.session() as session:
        report = await migrate_owner_data(
            session,
            source_id=ids["alice"],
            target_id=ids["bob"],
            platform="dingtalk",
            apply=True,
        )
    assert report["messages_moved"] == 1
    async with db.session() as session:
        assert await _owner(session, Message, ids["dingtalk"]) == ids["bob"]
        assert await _owner(session, Message, ids["moved"]) == ids["alice"]
    await db.dispose()


@pytest.mark.asyncio
async def test_migration_rejects_invalid_input(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed(db)

    async with db.session() as session:
        with pytest.raises(MigrationError):
            await migrate_owner_data(
                session, source_id=ids["alice"], target_id=ids["alice"]
            )
        with pytest.raises(MigrationError):
            await migrate_owner_data(
                session, source_id=ids["alice"], target_id="missing-user"
            )
    await db.dispose()

async def _seed_binding(db: Database):
    async with db.session() as session:
        alice = User(email="alice-bind@example.com", display_name="AliceB")
        bob = User(email="bob-bind@example.com", display_name="BobB")
        session.add_all([alice, bob])
        await session.flush()
        session.add(
            PlatformAccount(
                user_id=alice.id,
                platform="feishu",
                platform_user_id="ou_move_me",
                display_name="AliceB",
                status="connected",
            )
        )
        await session.commit()
        return {"alice": alice.id, "bob": bob.id}


@pytest.mark.asyncio
async def test_move_platform_binding_dry_run_then_apply(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed_binding(db)

    async with db.session() as session:
        report = await move_platform_binding(
            session, platform="feishu", handle="ou_move_me", target_id=ids["bob"]
        )
    assert report["status"] == "moved"
    assert report["applied"] is False
    async with db.session() as session:
        row = await session.scalar(
            select(PlatformAccount).where(PlatformAccount.platform_user_id == "ou_move_me")
        )
        assert row is not None and row.user_id == ids["alice"]

    async with db.session() as session:
        report = await move_platform_binding(
            session, platform="feishu", handle="ou_move_me", target_id=ids["bob"], apply=True
        )
    assert report["applied"] is True
    async with db.session() as session:
        row = await session.scalar(
            select(PlatformAccount).where(PlatformAccount.platform_user_id == "ou_move_me")
        )
        assert row is not None and row.user_id == ids["bob"]
    await db.dispose()


@pytest.mark.asyncio
async def test_move_platform_binding_rejects_conflicts(tmp_path: Path):
    settings = _settings(tmp_path)
    db = Database(settings)
    await db.init()
    ids = await _seed_binding(db)

    async with db.session() as session:
        report = await move_platform_binding(
            session, platform="feishu", handle="ou_move_me", target_id=ids["alice"]
        )
        assert report["status"] == "already_bound"

        with pytest.raises(MigrationError):
            await move_platform_binding(
                session, platform="feishu", handle="ou_missing", target_id=ids["bob"]
            )

        # Give Bob his own Feishu identity: the move must refuse instead of
        # violating the (user, platform) uniqueness rule.
        session.add(
            PlatformAccount(
                user_id=ids["bob"],
                platform="feishu",
                platform_user_id="ou_bob_own",
                display_name="BobB",
                status="connected",
            )
        )
        await session.commit()

    async with db.session() as session:
        with pytest.raises(MigrationError) as excinfo:
            await move_platform_binding(
                session, platform="feishu", handle="ou_move_me", target_id=ids["bob"], apply=True
            )
    assert "解绑" in str(excinfo.value)
    await db.dispose()
