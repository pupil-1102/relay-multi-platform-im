from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import Database
from ..models import Message, PlatformAccount, ProcessedEvent, User
from ..platform.base import PlatformError
from ..platform.feishu import message_type_label
from ..platform.registry import AdapterRegistry
from .accounts import context_for_user
from .emoji import normalize_feishu_emoji
from .events import EventBus
from .ingest import AttachmentData, InboundMessageData, ingest_message
from .resilience import platform_gate

logger = logging.getLogger(__name__)


def _chat_kind(chat: dict[str, Any]) -> str:
    mode = str(chat.get("chat_mode") or chat.get("chat_type") or "").lower()
    return "group" if mode in {"group", "topic"} else "direct"


def _content_message(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _post_text(content: dict[str, Any]) -> str:
    lines: list[str] = []
    title = str(content.get("title") or "").strip()
    if title:
        lines.append(title)
    for paragraph in content.get("content") or []:
        parts: list[str] = []
        for element in paragraph if isinstance(paragraph, list) else []:
            if not isinstance(element, dict):
                continue
            tag = element.get("tag")
            if tag == "text":
                parts.append(str(element.get("text") or ""))
            elif tag == "at":
                parts.append(f"@{element.get('user_name') or ''}")
            elif tag == "a":
                parts.append(str(element.get("text") or element.get("href") or ""))
            elif tag == "img":
                parts.append("[图片]")
        if parts:
            lines.append("".join(parts))
    return "\n".join(lines)


class FeishuUserMirror:
    """Poll the operator's own Feishu chats using their user OAuth token.

    Bot events only cover chats the bot participates in. This mirror is the
    supported path for the operator's personal p2p conversations; Feishu does
    not expose a personal-chat event stream, so it runs at a short poll
    interval and writes a normal inbound/outbound Message row.
    """

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._owner_id: str | None = None
        self.status: dict[str, Any] = {"state": "stopped", "detail": "", "polls": 0, "messages": 0}

    async def start(self, owner_id: str) -> None:
        if not self.settings.feishu_user_mirror_enabled:
            self.status = {"state": "disabled", "detail": "FEISHU_USER_MIRROR_ENABLED=false", "polls": 0, "messages": 0}
            return
        self._owner_id = owner_id
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="feishu-user-mirror")

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
        await asyncio.sleep(2)
        while self._running:
            try:
                await self._poll_once()
                self.status = {
                    **self.status,
                    "state": "running",
                    "detail": "个人账号消息轮询中",
                    "polls": self.status.get("polls", 0) + 1,
                }
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Feishu user mirror poll failed: %s", exc)
                self.status = {
                    **self.status,
                    "state": "error",
                    "detail": str(exc)[:300],
                    "polls": self.status.get("polls", 0) + 1,
                }
            await asyncio.sleep(max(self.settings.feishu_user_mirror_interval, 3))

    async def _poll_once(self) -> None:
        assert self._owner_id is not None
        async with self.db.session() as session:
            account = await context_for_user(
                session, self._owner_id, "feishu", self.settings
            )
        adapter = self.adapters.get("feishu")
        chats = await platform_gate.idempotent(
            "feishu", lambda: adapter.list_user_chats(account)
        )
        p2p_chats: list[dict[str, Any]] = []
        try:
            p2p_chats = await platform_gate.idempotent(
                "feishu", lambda: adapter.search_user_p2p_chats(account)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Feishu p2p chat discovery failed: %s", exc)
            self.status = {**self.status, "detail": f"p2p 会话发现失败：{str(exc)[:180]}"}
        known = {str(chat.get("chat_id") or "") for chat in chats}
        for chat in p2p_chats:
            if str(chat.get("chat_id") or "") not in known:
                chats.append(chat)
                known.add(str(chat.get("chat_id") or ""))
        if not chats:
            self.status = {**self.status, "detail": "未取到个人会话列表（需要 im:chat:readonly / search:message）"}
            return
        for chat in chats[:40]:
            if not self._running:
                return
            chat_id = str(chat.get("chat_id") or "")
            if not chat_id:
                continue
            try:
                await self._sync_chat(account, chat, chat_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Feishu personal chat sync failed chat=%s: %s", chat_id, exc)
            await asyncio.sleep(0.15)

    async def _sync_chat(self, account: Any, chat: dict[str, Any], chat_id: str) -> None:
        adapter = self.adapters.get("feishu")
        items = await platform_gate.idempotent(
            "feishu", lambda: adapter.list_user_chat_messages(account, chat_id)
        )
        if not items:
            return
        chat_type = _chat_kind(chat)
        chat_title = str(chat.get("name") or chat.get("chat_default_name") or "").strip()
        async with self.db.session() as session:
            owner_account = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.user_id == self._owner_id,
                    PlatformAccount.platform == "feishu",
                )
            )
            owner_open_id = owner_account.platform_user_id if owner_account else ""
            for item in reversed(items):
                if item.get("deleted"):
                    continue
                message_id = str(item.get("message_id") or "")
                if not message_id:
                    continue
                exists = await session.scalar(
                    select(Message.id).where(
                        Message.owner_id == self._owner_id,
                        Message.platform == "feishu",
                        Message.platform_msg_id == message_id,
                    )
                )
                if exists:
                    continue
                sent_by_relay = await session.scalar(
                    select(ProcessedEvent.id).where(
                        ProcessedEvent.platform == "feishu",
                        ProcessedEvent.event_id == f"sent:{message_id}",
                    )
                )
                if sent_by_relay:
                    # Relay sent this as the file's caption; it already has its
                    # own bubble.
                    continue
                sender = item.get("sender") or {}
                sender_id = str(sender.get("id") or "")
                direction = "outbound" if sender_id and sender_id == owner_open_id else "inbound"
                msg_type = str(item.get("msg_type") or "text")
                content = _content_message((item.get("body") or {}).get("content") or "{}")
                text = ""
                content_type = "text"
                attachments: list[AttachmentData] = []
                if msg_type == "text":
                    text = str(content.get("text") or "")
                    for mention in item.get("mentions") or []:
                        key = str(mention.get("key") or "")
                        name = str(mention.get("name") or "")
                        if key and name:
                            text = text.replace(key, f"@{name}")
                    text = re.sub(r"<at\b[^>]*>(.*?)</at>", r"@\1", text)
                    text = normalize_feishu_emoji(text)
                elif msg_type == "post":
                    content_type = "post"
                    text = normalize_feishu_emoji(_post_text(content))
                elif msg_type in {"image", "sticker"}:
                    key = str(content.get("image_key") or content.get("file_key") or "")
                    content_type = msg_type
                    if key:
                        try:
                            resource_type = "image" if msg_type == "image" else "file"
                            data, mime, filename = await adapter.download_message_resource(
                                account,
                                message_id,
                                key,
                                resource_type,
                                as_user=True,
                            )
                            attachments.append(
                                AttachmentData(
                                    filename=filename,
                                    mime_type=mime,
                                    size_bytes=len(data),
                                    platform_file_key=key,
                                    content=data,
                                )
                            )
                            if msg_type == "image":
                                text = "[图片]"
                        except Exception:  # noqa: BLE001
                            logger.warning("Feishu mirror image download failed", exc_info=True)
                            text = "[图片]" if msg_type == "image" else "[表情]"
                elif msg_type == "file":
                    key = str(content.get("file_key") or "")
                    text = str(content.get("file_name") or "")
                    content_type = "file"
                    if key:
                        try:
                            data, mime, filename = await adapter.download_message_resource(
                                account,
                                message_id,
                                key,
                                "file",
                                as_user=True,
                            )
                            attachments.append(
                                AttachmentData(
                                    filename=str(content.get("file_name") or filename),
                                    mime_type=mime,
                                    size_bytes=len(data),
                                    platform_file_key=key,
                                    content=data,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning("Feishu mirror file download failed", exc_info=True)
                elif msg_type == "folder":
                    # Feishu folder messages only carry a name and a file_key.
                    # The resource API answers 234043 "Unsupported message type"
                    # for them, so the best Relay can do is name the folder and
                    # point the operator at the client.
                    name = str(content.get("file_name") or "").strip() or "未命名文件夹"
                    key = str(content.get("file_key") or "")
                    content_type = "folder"
                    text = f"[文件夹] {name}"
                    if key:
                        try:
                            data, mime, filename = await adapter.download_message_resource(
                                account,
                                message_id,
                                key,
                                "file",
                                as_user=True,
                            )
                            attachments.append(
                                AttachmentData(
                                    filename=str(content.get("file_name") or filename),
                                    mime_type=mime,
                                    size_bytes=len(data),
                                    platform_file_key=key,
                                    content=data,
                                )
                            )
                        except PlatformError as exc:
                            logger.info("Feishu folder is not downloadable: %s", exc)
                            text = f"[文件夹] {name}（飞书接口不支持下载文件夹，请在飞书客户端查看）"
                else:
                    text = message_type_label(msg_type)
                    content_type = msg_type

                sender_name = ""
                if direction == "outbound":
                    sender_name = owner_account.display_name if owner_account else "我"
                else:
                    mapped = await session.scalar(
                        select(PlatformAccount).where(
                            PlatformAccount.platform == "feishu",
                            PlatformAccount.platform_user_id == sender_id,
                        )
                    )
                    if mapped:
                        user = await session.get(User, mapped.user_id)
                        sender_name = user.display_name if user else mapped.display_name
                    if not sender_name and chat_type == "direct" and chat_title:
                        sender_name = chat_title
                    if not sender_name:
                        sender_name = "外部联系人" if chat_type == "direct" else "飞书成员"

                created = None
                try:
                    created = datetime.fromtimestamp(
                        int(item.get("create_time") or 0) / 1000, tz=UTC
                    )
                except (TypeError, ValueError, OSError):
                    created = None
                raw = dict(item)
                if chat_title:
                    raw["conversation_title"] = chat_title
                raw["mirror"] = True
                await ingest_message(
                    session,
                    settings=self.settings,
                    bus=self.bus,
                    owner_id=self._owner_id,
                    adapters=self.adapters,
                    data=InboundMessageData(
                        platform="feishu",
                        platform_msg_id=message_id,
                        text=text,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        sender_platform_id=sender_id,
                        sender_name=sender_name,
                        content_type=content_type,
                        direction=direction,
                        platform_created_at=created,
                        raw=raw,
                        attachments=attachments,
                    ),
                )
                self.status = {
                    **self.status,
                    "messages": self.status.get("messages", 0) + 1,
                }
