from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models import Direction, Message, PlatformAccount, ProcessedEvent, User
from backend.app.platform.base import AccountContext, PlatformError
from backend.app.platform.mock import MockAdapter
from backend.app.platform.registry import AdapterRegistry
from backend.app.security import encrypt_secret
from backend.app.services.events import EventBus
from backend.app.services.feishu_mirror import FeishuUserMirror
from backend.app.services.send import send_file

# tmp_path is unusable under the DSH Windows sandbox (pytest's numbered temp
# dirs cannot be cleaned up), so these tests use a scratch dir in the repo.
SCRATCH = Path(__file__).resolve().parents[2] / ".tmp"


class FakeFeishu:
    platform = "feishu"

    def __init__(self, message: dict, *, download_error: PlatformError) -> None:
        self.message = message
        self.download_error = download_error
        self.downloads: list[tuple[str, str, str]] = []

    async def list_user_chat_messages(self, account: AccountContext, chat_id: str) -> list[dict]:
        return [self.message]

    async def download_message_resource(
        self,
        account: AccountContext,
        message_id: str,
        file_key: str,
        resource_type: str,
        *,
        as_user: bool = False,
    ):
        self.downloads.append((message_id, file_key, resource_type))
        raise self.download_error


def _folder_message() -> dict:
    return {
        "message_id": "om_folder_1",
        "msg_type": "folder",
        "body": {"content": json.dumps({"file_key": "file_v3_folder", "file_name": "04-tools"})},
        "chat_id": "oc_chat_1",
        "sender": {"id": "ou_peer"},
        "create_time": "1795171910044",
    }


@asynccontextmanager
async def mirror_for(message: dict, error: PlatformError) -> AsyncIterator[tuple]:
    root = SCRATCH / f"feishu-inbound-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(root / 'mirror.db').as_posix()}",
        data_dir=root,
        file_dir=root / "files",
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        feishu_user_mirror_enabled=True,
    )
    db = Database(settings)
    await db.init()
    async with db.session() as session:
        owner = User(display_name="演示用户")
        session.add(owner)
        await session.flush()
        session.add(
            PlatformAccount(
                user_id=owner.id,
                platform="feishu",
                platform_user_id="ou_owner",
                display_name="演示用户",
            )
        )
        await session.commit()
        owner_id = owner.id
    adapter = FakeFeishu(message, download_error=error)
    mirror = FeishuUserMirror(
        settings, db, AdapterRegistry({"feishu": adapter}, demo_mode=False), EventBus()
    )
    mirror._owner_id = owner_id
    account = AccountContext(
        platform="feishu",
        user_id=owner_id,
        platform_user_id="ou_owner",
        display_name="演示用户",
        access_token="user-token",
    )
    try:
        yield db, adapter, mirror, account
    finally:
        await db.dispose()
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_feishu_folder_message_is_kept_with_a_readable_label():
    async with mirror_for(
        _folder_message(),
        PlatformError("飞书资源下载失败: 234043 Unsupported message type", code="feishu_234043"),
    ) as (db, adapter, mirror, account):
        await mirror._sync_chat(account, {"chat_id": "oc_chat_1", "name": "郑会润"}, "oc_chat_1")
        async with db.session() as session:
            row = (await session.execute(select(Message))).scalar_one()
        assert row.content_type == "folder"
        assert "04-tools" in row.text
        assert "飞书接口不支持下载文件夹" in row.text
        assert row.platform_msg_id == "om_folder_1"
        assert adapter.downloads == [("om_folder_1", "file_v3_folder", "file")]


@pytest.mark.asyncio
async def test_demo_mode_still_sends_feishu_files_in_a_direct_chat():
    """Demo mode has no operator account; the bot path must stay available."""
    root = SCRATCH / f"demo-send-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(root / 'demo.db').as_posix()}",
        data_dir=root,
        file_dir=root / "files",
        demo_mode=True,
        seed_demo=False,
    )
    db = Database(settings)
    await db.init()
    try:
        async with db.session() as session:
            owner = User(display_name="演示用户")
            session.add(owner)
            await session.commit()
            payload = await send_file(
                session,
                settings=settings,
                adapters=AdapterRegistry({"feishu": MockAdapter("feishu")}, demo_mode=True),
                bus=EventBus(),
                owner=owner,
                platform="feishu",
                filename="demo.txt",
                mime_type="text/plain",
                data=b"demo",
                to_handle="oc_demo_chat",
                chat_id="oc_demo_chat",
                chat_type="direct",
            )
        assert payload["delivery_status"] == "sent"
    finally:
        await db.dispose()
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_feishu_direct_chat_file_send_infers_the_operator_identity():
    """Replies from the inbox composer carry no chat_type; the history must win."""
    root = SCRATCH / f"real-send-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(root / 'real.db').as_posix()}",
        data_dir=root,
        file_dir=root / "files",
        demo_mode=False,
        seed_demo=False,
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
    )
    db = Database(settings)
    await db.init()
    adapter = MockAdapter("feishu")
    try:
        async with db.session() as session:
            owner = User(display_name="演示用户")
            session.add(owner)
            await session.flush()
            session.add(
                PlatformAccount(
                    user_id=owner.id,
                    platform="feishu",
                    platform_user_id="ou_owner",
                    display_name="演示用户",
                    access_token_enc=encrypt_secret("user-token", settings),
                )
            )
            session.add(
                Message(
                    owner_id=owner.id,
                    platform="feishu",
                    direction=Direction.INBOUND,
                    platform_chat_id="oc_direct_chat",
                    chat_type="direct",
                    sender_name="郑会润",
                    content_type="text",
                    text="发个文件给我",
                    delivery_status="received",
                )
            )
            await session.commit()
            payload = await send_file(
                session,
                settings=settings,
                adapters=AdapterRegistry({"feishu": adapter}, demo_mode=False),
                bus=EventBus(),
                owner=owner,
                platform="feishu",
                filename="要求.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data=b"docx",
                to_handle="oc_direct_chat",
                client_msg_id="client-direct-file",
            )
        assert payload["delivery_status"] == "sent"
        assert payload["chat_type"] == "direct"
        assert adapter.outbox[-1]["as_user"] is True
    finally:
        await db.dispose()
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_feishu_mirror_skips_the_caption_it_sent_with_a_file():
    message = {
        "message_id": "om_caption_1",
        "msg_type": "text",
        "body": {"content": json.dumps({"text": "【Relay 测试】文件发送链路验证"})},
        "chat_id": "oc_chat_1",
        "sender": {"id": "ou_owner"},
        "create_time": "1795171910044",
    }
    async with mirror_for(message, PlatformError("boom", code="feishu_resource_download")) as (
        db,
        _adapter,
        mirror,
        account,
    ):
        async with db.session() as session:
            session.add(
                ProcessedEvent(
                    platform="feishu",
                    event_id="sent:om_caption_1",
                    event_type="outbound_text",
                    outcome="sent",
                )
            )
            await session.commit()
        await mirror._sync_chat(account, {"chat_id": "oc_chat_1", "name": "郑会润"}, "oc_chat_1")
        async with db.session() as session:
            rows = (await session.execute(select(Message))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_feishu_unknown_message_type_gets_a_readable_placeholder():
    message = {
        "message_id": "om_card_1",
        "msg_type": "interactive",
        "body": {"content": json.dumps({"card": {}})},
        "chat_id": "oc_chat_1",
        "sender": {"id": "ou_peer"},
        "create_time": "1795171910044",
    }
    async with mirror_for(message, PlatformError("boom", code="feishu_resource_download")) as (
        db,
        _adapter,
        mirror,
        account,
    ):
        await mirror._sync_chat(account, {"chat_id": "oc_chat_1", "name": "郑会润"}, "oc_chat_1")
        async with db.session() as session:
            row = (await session.execute(select(Message))).scalar_one()
        assert row.text == "[卡片消息]"
        assert row.content_type == "interactive"
