from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import Settings
from ..db import Database
from ..models import Message, PlatformAccount
from ..platform.registry import AdapterRegistry
from .events import EventBus
from .ingest import AttachmentData, InboundMessageData, ingest_message

logger = logging.getLogger(__name__)


class DingTalkDwsMirror:
    """Mirror personal DingTalk chats through the authenticated DWS CLI.

    DingTalk group bots only receive @-messages, so normal group media never
    reaches the Stream bot. DWS sees the user's own conversations and can
    download message resources; this poller fills that gap.
    """

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._owner_id: str | None = None
        self._last_poll: datetime | None = None
        self._owner_open_id = ""
        self.status: dict[str, Any] = {"state": "stopped", "detail": "", "polls": 0, "messages": 0}

    async def start(self, owner_id: str) -> None:
        if not self.settings.dingtalk_dws_im_mirror_enabled:
            self.status = {"state": "disabled", "detail": "DINGTALK_DWS_IM_MIRROR_ENABLED=false", "polls": 0, "messages": 0}
            return
        self._owner_id = owner_id
        self._running = True
        self._last_poll = datetime.now().astimezone().replace(microsecond=0) - timedelta(hours=24)
        Path(".dws-im-downloads").mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop(), name="dingtalk-dws-im-mirror")

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
        await asyncio.sleep(3)
        while self._running:
            try:
                await self._poll_once()
                self.status = {
                    **self.status,
                    "state": "running",
                    "detail": "DWS 个人 IM 轮询中",
                    "polls": self.status.get("polls", 0) + 1,
                }
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("DingTalk DWS IM mirror failed: %s", exc)
                self.status = {
                    **self.status,
                    "state": "error",
                    "detail": str(exc)[:300],
                    "polls": self.status.get("polls", 0) + 1,
                }
            await asyncio.sleep(max(self.settings.dingtalk_dws_im_interval, 5))

    async def _poll_once(self) -> None:
        assert self._owner_id is not None
        now = datetime.now().astimezone().replace(microsecond=0)
        start = (self._last_poll or now - timedelta(hours=24)) - timedelta(seconds=90)
        start = start.replace(microsecond=0)
        conversations = await self._dws_json(
            [
                "chat",
                "+recent-conversations",
                "--start",
                start.isoformat(),
                "--format",
                "json",
            ]
        )
        data = conversations.get("data") if isinstance(conversations.get("data"), dict) else {}
        rows = data.get("conversations") or []
        for conversation in rows[:30]:
            if not self._running:
                return
            chat_id = str(conversation.get("conversationId") or "")
            if not chat_id:
                continue
            try:
                await self._sync_conversation(
                    chat_id,
                    str(conversation.get("name") or ""),
                    str(conversation.get("type") or "group"),
                    start,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DingTalk DWS conversation sync failed %s: %s", chat_id, exc)
            await asyncio.sleep(0.1)
        self._last_poll = now

    async def _sync_conversation(self, chat_id: str, title: str, kind: str, start: datetime) -> None:
        payload = await self._dws_json(
            [
                "chat",
                "+chat-messages",
                "--group",
                chat_id,
                "--start",
                start.isoformat(),
                "--no-reactions",
                "--format",
                "json",
            ]
        )
        messages = payload.get("messages") or []
        async with self.db.session() as session:
            owner = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.user_id == self._owner_id,
                    PlatformAccount.platform == "dingtalk",
                )
            )
            if owner is None:
                return
            if not self._owner_open_id:
                self._owner_open_id = await self._resolve_owner_open_id(owner)
            for item in reversed(messages):
                if not self._running:
                    return
                message_id = str(item.get("messageId") or "")
                if not message_id:
                    continue
                exists = await session.scalar(
                    select(Message.id).where(
                        Message.owner_id == self._owner_id,
                        Message.platform == "dingtalk",
                        Message.platform_msg_id == message_id,
                    )
                )
                if exists:
                    continue
                sender_id = str(item.get("senderId") or "")
                direction = "outbound" if sender_id and sender_id == self._owner_open_id else "inbound"
                text = str(item.get("text") or "")
                content_type = "text"
                attachments: list[AttachmentData] = []
                refs = item.get("resourceRefs") or []
                image_ref = next(
                    (
                        ref
                        for ref in refs
                        if str(ref.get("contentType") or "").lower() == "image"
                    ),
                    None,
                )
                file_ref = next(
                    (
                        ref
                        for ref in refs
                        if str(ref.get("contentType") or "").lower() == "file"
                        or str(ref.get("resourceIdType") or "").lower() == "fileid"
                    ),
                    None,
                )
                if image_ref:
                    content_type = "image"
                    text = "[图片]"
                    attachment = await self._download_ref(
                        chat_id, message_id, image_ref, f"image-{message_id[:10]}"
                    )
                    if attachment:
                        attachments.append(attachment)
                elif file_ref:
                    content_type = "file"
                    filename = str(file_ref.get("name") or "钉钉文件")
                    text = filename
                    attachment = await self._download_ref(
                        chat_id, message_id, file_ref, filename
                    )
                    if attachment:
                        attachment.filename = filename
                        attachments.append(attachment)
                elif text.startswith("[图片消息]"):
                    content_type = "image"
                    text = "[图片]"
                elif text.startswith("[文件]"):
                    content_type = "file"

                created = self._parse_time(str(item.get("createTime") or ""))
                if direction == "outbound" and await self._has_relay_duplicate(
                    session, chat_id, text, created
                ):
                    continue
                raw = dict(item)
                if title:
                    raw["conversation_title"] = title
                raw["mirror"] = True
                await ingest_message(
                    session,
                    settings=self.settings,
                    bus=self.bus,
                    owner_id=self._owner_id,
                    adapters=self.adapters,
                    data=InboundMessageData(
                        platform="dingtalk",
                        platform_msg_id=message_id,
                        text=text,
                        chat_id=chat_id,
                        chat_type="group" if kind == "group" else "direct",
                        sender_platform_id=sender_id,
                        sender_name=str(item.get("sender") or ("我" if direction == "outbound" else "钉钉用户")),
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

    async def _has_relay_duplicate(
        self,
        session,
        chat_id: str,
        text: str,
        created: datetime | None,
    ) -> bool:
        if not text.strip():
            return False
        normalized = re.sub(r"\s+", "", text)
        rows = (
            await session.scalars(
                select(Message).where(
                    Message.owner_id == self._owner_id,
                    Message.platform == "dingtalk",
                    Message.platform_chat_id == chat_id,
                    Message.direction == "outbound",
                )
            )
        ).all()
        for row in rows:
            if '"mirror"' in (row.raw_json or ""):
                continue
            if re.sub(r"\s+", "", row.text or "") != normalized:
                continue
            if created is None or row.created_at is None:
                return True
            left = row.created_at
            right = created
            if left.tzinfo is None:
                left = left.replace(tzinfo=UTC)
            if right.tzinfo is None:
                right = right.replace(tzinfo=UTC)
            if abs((left - right).total_seconds()) <= 300:
                return True
        return False

    async def _download_ref(
        self, chat_id: str, message_id: str, ref: dict[str, Any], filename: str
    ) -> AttachmentData | None:
        resource_id = str(ref.get("resourceId") or "")
        resource_type = str(ref.get("resourceIdType") or "mediaId")
        if not resource_id:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:80] or "resource.bin"
        relative = f".dws-im-downloads/{message_id[:16]}-{safe}"
        payload = await self._dws_json(
            [
                "chat",
                "+messages-resource-download",
                "--resource-id",
                resource_id,
                "--message-id",
                message_id,
                "--open-conversation-id",
                chat_id,
                "--type",
                "fileId" if resource_type.lower() == "fileid" else "mediaId",
                "--output",
                relative,
                "--format",
                "json",
            ]
        )
        local_path = str(payload.get("localPath") or relative)
        path = Path(local_path)
        if not path.exists():
            return None
        data = path.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if mime == "application/octet-stream":
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif data.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif data.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                mime = "image/webp"
        return AttachmentData(
            filename=filename,
            mime_type=mime,
            size_bytes=len(data),
            platform_file_key=resource_id,
            content=data,
        )

    async def _resolve_owner_open_id(self, owner: PlatformAccount) -> str:
        profile = await self._dws_json(["contact", "+me", "--format", "json"])
        data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
        keyword = str(data.get("mobile") or data.get("name") or owner.display_name or "")
        if not keyword:
            return ""
        result = await self._dws_json(
            ["contact", "user", "search", "--query", keyword, "--format", "json"]
        )
        for row in result.get("result") or []:
            if str(row.get("userId") or "") == owner.platform_user_id:
                return str(row.get("openDingTalkId") or "")
        return ""

    async def _dws_json(self, args: list[str]) -> dict[str, Any]:
        command = self.settings.dingtalk_dws_command
        env = os.environ.copy()
        if self.settings.dingtalk_dws_config_dir:
            env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except FileNotFoundError as exc:
            raise RuntimeError(f"找不到 dws 命令：{command}") from exc
        except asyncio.TimeoutError as exc:
            raise RuntimeError("DWS 个人 IM 查询超时") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                (stderr.decode("utf-8", "ignore") or stdout.decode("utf-8", "ignore"))[:400]
            )
        try:
            return json.loads(stdout.decode("utf-8", "ignore").strip() or "{}")
        except ValueError as exc:
            raise RuntimeError("DWS 个人 IM 返回非 JSON") from exc

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        if not value:
            return None
        for text in (value, value.replace(" ", "T")):
            try:
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
                return parsed
            except ValueError:
                continue
        return None
