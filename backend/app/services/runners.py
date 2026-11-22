from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from ..config import Settings
from ..db import Database
from ..models import PlatformAccount, TaskLink, User
from ..platform.base import PlatformError
from ..platform.feishu import message_type_label
from ..platform.registry import AdapterRegistry
from .events import EventBus
from .emoji import normalize_feishu_emoji
from .ingest import AttachmentData, InboundMessageData, TaskEventData, ingest_message, ingest_task_event
from .resilience import platform_gate

from sqlalchemy import select

logger = logging.getLogger(__name__)


class FeishuRunner:
    """Feishu message + task event long connection.

    lark-oapi >= 1.7.3 is required: older 1.5.x/1.6.x clients can connect but
    receive no WebSocket event pushes. We also register the tenant v1 task
    event as a fallback; the v2 ``task.task.update_user_access_v2`` key is
    registered through ``register_p2_customized_event``.
    """

    platform = "feishu"

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus, owner_id: str) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self.owner_id = owner_id
        self.client: Any = None
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self.status: dict[str, Any] = {"state": "starting", "detail": ""}

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stop.clear()
        try:
            import lark_oapi as lark
        except ImportError:
            self.status = {"state": "error", "detail": "lark-oapi 未安装"}
            logger.warning("lark-oapi not installed, Feishu runner disabled")
            return
        try:
            await self.adapters.get("feishu").health_check()
        except PlatformError as exc:
            self.status = {"state": "error", "detail": f"{exc.code}: {exc.message}"}
            logger.error("Feishu credential preflight failed: %s", exc.message)
            return
        self.status = {"state": "starting", "detail": "凭证有效，正在连接长连接"}
        builder = lark.EventDispatcherHandler.builder(
            self.settings.feishu_encrypt_key or "", self.settings.feishu_verification_token or ""
        ).register_p2_im_message_receive_v1(self._on_message)
        try:
            builder = builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(
                self._on_p2p_chat_entered
            )
        except (AttributeError, TypeError):
            logger.warning("lark-oapi lacks bot_p2p_chat_entered handler")
        try:
            builder = builder.register_p2_customized_event(
                "task.task.update_user_access_v2", self._on_task_v2
            )
        except Exception:  # noqa: BLE001
            logger.info("lark-oapi customized task event registration failed")
        try:
            builder = builder.register_p2_task_task_update_tenant_v1(self._on_task_event)
        except Exception:  # noqa: BLE001
            logger.info("lark-oapi has no v1 task event processor")
        handler = builder.build()
        self.client = lark.ws.Client(
            self.settings.feishu_app_id,
            self.settings.feishu_app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        self.thread = threading.Thread(target=self._run, daemon=True, name="feishu-ws")
        self.thread.start()
        self.status = {"state": "running", "detail": "长连接线程已启动"}
        # Do not block application startup on the task-subscription HTTP call;
        # it can be slow or rate-limited and is not required for message events.
        self._subscribe_task = asyncio.create_task(self._subscribe_tasks())

    async def _subscribe_tasks(self) -> None:
        async with self.db.session() as session:
            account = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.user_id == self.owner_id,
                    PlatformAccount.platform == "feishu",
                )
            )
            if account is None:
                return
            try:
                from .tokens import ensure_fresh_account

                context = await ensure_fresh_account(session, account, self.settings)
                await platform_gate.idempotent(
                    "feishu",
                    lambda: self.adapters.get("feishu").subscribe_user_tasks(context),
                )
                logger.info("Feishu user task subscription requested")
            except Exception:  # noqa: BLE001
                logger.warning("Feishu user task subscription failed (will rely on live events)", exc_info=True)

    def _run(self) -> None:
        # lark-oapi's WebSocket client expects to own the event loop of the
        # thread it runs in. Creating and publishing that loop is required when
        # the client is started from a background thread (the bridge does the
        # same thing); otherwise start() sees uvicorn's running loop and fails
        # with "This event loop is already running".
        while not self._stop.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                import lark_oapi.ws.client as ws_client_module

                ws_client_module.loop = loop
            except (ImportError, AttributeError):
                pass
            try:
                self.client.start()
            except Exception:  # noqa: BLE001
                logger.exception("Feishu WebSocket stopped")
            finally:
                try:
                    loop.close()
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                break
            self.status = {"state": "reconnecting", "detail": "飞书长连接断开，3s 后重连"}
            time.sleep(3)
        self.status = {"state": "stopped", "detail": "飞书长连接已停止"}

    async def stop(self) -> None:
        self._stop.set()
        if self.client:
            try:
                self.client.stop()
            except Exception:  # noqa: BLE001
                pass
        self.client = None

    def _schedule(self, coro) -> None:
        if self.loop is None:
            logger.warning("Feishu runner has no event loop")
            return
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _on_p2p_chat_entered(self, data: Any) -> None:
        try:
            event = data.event
            chat_id = str(getattr(event, "chat_id", "") or "")
            operator = getattr(event, "operator_id", None)
            open_id = str(getattr(operator, "open_id", "") or "")
            logger.info(
                "Feishu p2p chat entered chat_id=%s open_id=%s",
                chat_id,
                open_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Feishu p2p chat entered parse failed")

    def _on_message(self, data: Any) -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender
            sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "") or ""
            payload = {
                "message_id": message.message_id,
                "message_type": message.message_type,
                "content": message.content,
                "chat_id": message.chat_id,
                "chat_type": "group" if message.chat_type == "group" else "direct",
                "sender_id": sender_id,
                "sender_name": "",
                "mentions": [
                    {"key": getattr(item, "key", ""), "name": getattr(item, "name", "")}
                    for item in (getattr(message, "mentions", None) or [])
                ],
                "create_time": data.header.create_time if data.header else "",
            }
            logger.info(
                "Feishu message event chat_type=%s chat_id=%s msg_type=%s",
                payload["chat_type"],
                payload["chat_id"],
                payload["message_type"],
            )
            self._schedule(self._ingest_message(payload))
        except Exception:  # noqa: BLE001
            logger.exception("Feishu message parse failed")

    async def _resolve_sender_name(self, sender_id: str) -> str:
        if not sender_id:
            return ""
        async with self.db.session() as session:
            account = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.platform == "feishu",
                    PlatformAccount.platform_user_id == sender_id,
                )
            )
            if account:
                user = await session.get(User, account.user_id)
                if user and user.display_name:
                    return user.display_name
            owner = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.user_id == self.owner_id,
                    PlatformAccount.platform == "feishu",
                )
            )
            if owner is None:
                return ""
            try:
                from .accounts import context_for_user

                context = await context_for_user(session, self.owner_id, "feishu", self.settings)
                resolver = getattr(self.adapters.get("feishu"), "get_user_name", None)
                if resolver is None:
                    return ""
                return await resolver(context, sender_id)
            except Exception:  # noqa: BLE001
                logger.debug("Feishu sender name resolution failed", exc_info=True)
                return ""

    async def _ingest_message(self, payload: dict[str, Any]) -> None:
        if not payload.get("sender_name") and payload.get("sender_id"):
            payload["sender_name"] = await self._resolve_sender_name(payload["sender_id"])
        text = ""
        content_type = "text"
        try:
            content = json.loads(payload.get("content") or "{}")
        except ValueError:
            content = {}
        if payload["message_type"] == "text":
            text = content.get("text", "")
            for mention in payload.get("mentions") or []:
                key = str(mention.get("key") or "")
                name = str(mention.get("name") or "")
                if key and name:
                    text = text.replace(key, f"@{name}")
            import re

            text = re.sub(r"<at\b[^>]*>(.*?)</at>", r"@\1", text)
            text = normalize_feishu_emoji(text)
        elif payload["message_type"] == "file":
            text = content.get("file_name", "")
            content_type = "file"
        elif payload["message_type"] == "image":
            text = "[图片]"
            content_type = "image"
        elif payload["message_type"] == "sticker":
            text = ""
            content_type = "sticker"
        elif payload["message_type"] == "folder":
            # Folder messages expose only a name; the resource API rejects them
            # with 234043 "Unsupported message type".
            name = str(content.get("file_name") or "").strip() or "未命名文件夹"
            text = f"[文件夹] {name}（飞书接口不支持下载文件夹，请在飞书客户端查看）"
            content_type = "folder"
        else:
            text = message_type_label(payload["message_type"])
            content_type = str(payload["message_type"])

        attachments: list[AttachmentData] = []
        if payload["message_type"] == "file" and content.get("file_key"):
            attachments.append(
                AttachmentData(
                    filename=content.get("file_name", "飞书文件"),
                    platform_file_key=content.get("file_key"),
                )
            )

        async with self.db.session() as session:
            resource_key = content.get("file_key") if payload["message_type"] == "sticker" else content.get("image_key")
            if payload["message_type"] in {"image", "sticker"} and resource_key:
                try:
                    from .accounts import context_for_user

                    account = await context_for_user(
                        session, self.owner_id, "feishu", self.settings
                    )
                    resource_type = "file" if payload["message_type"] == "sticker" else "image"
                    image_data, mime_type, filename = await self.adapters.get(
                        "feishu"
                    ).download_message_resource(
                        account,
                        str(payload["message_id"]),
                        str(resource_key),
                        resource_type,
                    )
                    attachments.append(
                        AttachmentData(
                            filename=filename,
                            mime_type=mime_type,
                            size_bytes=len(image_data),
                            platform_file_key=str(resource_key),
                            content=image_data,
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("Feishu image/sticker download failed", exc_info=True)
                    if payload["message_type"] == "sticker":
                        text = "[表情]"

            await ingest_message(
                session,
                settings=self.settings,
                bus=self.bus,
                owner_id=self.owner_id,
                adapters=self.adapters,
                data=InboundMessageData(
                    platform="feishu",
                    platform_msg_id=payload["message_id"],
                    text=text,
                    chat_id=payload.get("chat_id"),
                    chat_type=payload.get("chat_type", "direct"),
                    sender_platform_id=payload.get("sender_id"),
                    sender_name=payload.get("sender_name") or "飞书用户",
                    content_type=content_type,
                    raw=payload,
                    attachments=attachments,
                ),
            )

    def _on_task_v2(self, data: Any) -> None:
        try:
            event = data.event or {}
            task_id = str(event.get("task_guid") or event.get("task_id") or "")
            if not task_id:
                return
            event_id = data.header.event_id if data.header else f"feishu-v2-task-{task_id}"
            event_types = ",".join(event.get("event_types") or [])
            self._schedule(self._ingest_task_event(task_id, event_id, event_type=f"task.v2.user_access[{event_types}]"))
        except Exception:  # noqa: BLE001
            logger.exception("Feishu v2 task event parse failed")

    def _on_task_event(self, data: Any) -> None:
        try:
            task_id = str(data.event.task_id)
            event_id = data.header.event_id if data.header else f"feishu-task-{task_id}"
            self._schedule(self._ingest_task_event(task_id, event_id, event_type="task.v1.tenant_update"))
        except Exception:  # noqa: BLE001
            logger.exception("Feishu task event parse failed")

    async def _ingest_task_event(self, task_id: str, event_id: str, event_type: str = "task.updated") -> None:
        async with self.db.session() as session:
            link = await session.scalar(
                select(TaskLink).where(
                    TaskLink.platform == "feishu", TaskLink.platform_task_id == task_id
                )
            )
            status = "open"
            title = f"飞书任务 {task_id}"
            account = await session.scalar(
                select(PlatformAccount).where(
                    PlatformAccount.user_id == self.owner_id,
                    PlatformAccount.platform == "feishu",
                )
            )
            if account is not None:
                try:
                    from .tokens import ensure_fresh_account

                    context = await ensure_fresh_account(session, account, self.settings)
                    state = await platform_gate.idempotent(
                        "feishu",
                        lambda: self.adapters.get("feishu").fetch_task(
                            context,
                            SimpleNamespace(platform_task_id=task_id),
                        ),
                    )
                    status = state.status
                    title = state.title or title
                except Exception:  # noqa: BLE001
                    logger.info("Feishu task detail fetch failed for %s", task_id, exc_info=True)
            await ingest_task_event(
                session,
                settings=self.settings,
                adapters=self.adapters,
                bus=self.bus,
                owner_id=self.owner_id,
                data=TaskEventData(
                    platform="feishu",
                    event_id=event_id,
                    platform_task_id=task_id,
                    status=status,
                    event_type=event_type,
                    title=title,
                    occurred_at=datetime.now(UTC),
                    raw={"source": "feishu-ws", "event_type": event_type},
                ),
            )


class DingTalkRunner:
    platform = "dingtalk"

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus, owner_id: str) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self.owner_id = owner_id
        self.client: Any = None
        self.task: asyncio.Task | None = None
        self.status: dict[str, Any] = {"state": "starting", "detail": ""}

    async def start(self) -> None:
        try:
            import dingtalk_stream
        except ImportError:
            self.status = {"state": "error", "detail": "dingtalk-stream 未安装"}
            logger.warning("dingtalk-stream not installed, DingTalk runner disabled")
            return

        runner = self

        class Handler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback):
                message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                text = ""
                try:
                    text = message.text.content if message.text else ""
                except AttributeError:
                    pass
                is_group = str(getattr(message, "conversation_type", "1")) == "2"
                raw_content = callback.data.get("content") or {}
                if isinstance(raw_content, str):
                    try:
                        raw_content = json.loads(raw_content)
                    except ValueError:
                        raw_content = {}
                msg_type = str(getattr(message, "message_type", "text") or "text")
                content_type = "text"
                attachments: list[AttachmentData] = []
                if msg_type == "picture":
                    content_type = "image"
                    text = "[图片]"
                    code = getattr(getattr(message, "image_content", None), "download_code", "")
                    if code:
                        try:
                            data, mime = await runner._download_media(str(code))
                            ext = "png" if "png" in mime else "jpg" if "jpeg" in mime else "gif" if "gif" in mime else "webp" if "webp" in mime else "bin"
                            attachments.append(
                                AttachmentData(
                                    filename=f"dingtalk-image-{str(message.message_id)[:8]}.{ext}",
                                    mime_type=mime,
                                    size_bytes=len(data),
                                    platform_file_key=str(code),
                                    content=data,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning("DingTalk image download failed", exc_info=True)
                elif msg_type == "file":
                    content_type = "file"
                    code = str(raw_content.get("downloadCode") or "")
                    filename = str(raw_content.get("fileName") or "钉钉文件")
                    text = filename
                    if code:
                        try:
                            data, mime = await runner._download_media(code)
                            attachments.append(
                                AttachmentData(
                                    filename=filename,
                                    mime_type=mime,
                                    size_bytes=len(data),
                                    platform_file_key=code,
                                    content=data,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning("DingTalk file download failed", exc_info=True)
                elif msg_type == "richText":
                    try:
                        text = "\n".join(message.get_text_list() or [])
                    except Exception:  # noqa: BLE001
                        text = ""
                await runner._ingest(
                    InboundMessageData(
                        platform="dingtalk",
                        platform_msg_id=str(message.message_id),
                        text=text,
                        chat_id=str(message.conversation_id),
                        chat_type="group" if is_group else "direct",
                        sender_platform_id=str(message.sender_staff_id or message.sender_id),
                        sender_name=str(message.sender_nick or "钉钉用户"),
                        content_type=content_type,
                        attachments=attachments,
                        raw={
                            "message_id": message.message_id,
                            "message_type": msg_type,
                            "conversation_id": message.conversation_id,
                            "conversation_title": getattr(message, "conversation_title", "") or "",
                        },
                    )
                )
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        credential = dingtalk_stream.Credential(self.settings.dingtalk_client_id, self.settings.dingtalk_client_secret)
        self.client = dingtalk_stream.DingTalkStreamClient(credential)
        self.client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, Handler())
        loop = asyncio.get_running_loop()
        self.task = asyncio.ensure_future(loop.run_in_executor(None, self.client.start_forever))
        self.status = {"state": "running", "detail": "钉钉 Stream 线程已启动"}

    async def _download_media(self, download_code: str) -> tuple[bytes, str]:
        from .accounts import context_for_user

        async with self.db.session() as session:
            account = await context_for_user(
                session, self.owner_id, "dingtalk", self.settings
            )
            return await self.adapters.get("dingtalk").download_media(
                account, download_code
            )

    async def _ingest(self, data: InboundMessageData) -> None:
        async with self.db.session() as session:
            await ingest_message(
                session,
                settings=self.settings,
                bus=self.bus,
                owner_id=self.owner_id,
                adapters=self.adapters,
                data=data,
            )

    async def stop(self) -> None:
        if self.client:
            try:
                self.client.stop()
            except Exception:  # noqa: BLE001
                pass


class WeComRunner:
    """WeCom intelligent-robot (aibot) WebSocket transport."""

    platform = "wecom"
    MAX_CACHED_FRAMES = 200

    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus, owner_id: str) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self.owner_id = owner_id
        self.client: Any = None
        self.task: asyncio.Task | None = None
        self.status: dict[str, Any] = {"state": "starting", "detail": ""}
        # WeCom answers a bot message by replying to its frame (req_id), so the
        # frames have to stay reachable until the operator replies.
        self.frames: dict[str, dict[str, Any]] = {}
        self._frame_order: list[str] = []

    def remember_frame(self, msgid: str, frame: Any) -> None:
        if not msgid:
            return
        if msgid not in self.frames:
            self._frame_order.append(msgid)
            while len(self._frame_order) > self.MAX_CACHED_FRAMES:
                self.frames.pop(self._frame_order.pop(0), None)
        self.frames[msgid] = frame if isinstance(frame, dict) else dict(frame)

    def has_frame(self, msgid: str) -> bool:
        return bool(msgid) and msgid in self.frames

    async def reply_in_conversation(self, msgid: str, text: str) -> None:
        frame = self.frames.get(msgid)
        if frame is None:
            raise PlatformError("企微 aibot 消息帧已失效", code="wecom_aibot_frame_missing")
        if self.client is None:
            raise PlatformError("企微 aibot 未连接", code="wecom_aibot_offline")
        await self.client.reply(frame, {"msgtype": "text", "text": {"content": text}})
        self.status = {**self.status, "replies": self.status.get("replies", 0) + 1}

    async def send_to_chat(self, chat_id: str, text: str) -> None:
        """Proactive aibot send for the WeCom composer."""
        if self.client is None:
            raise PlatformError("企微 aibot 未连接", code="wecom_aibot_offline")
        await self.client.send_message(
            chat_id,
            {"msgtype": "markdown", "markdown": {"content": text}},
        )
        self.status = {**self.status, "replies": self.status.get("replies", 0) + 1}

    async def send_media(
        self, chat_id: str, data: bytes, mime: str, filename: str
    ) -> None:
        """Proactive aibot media send for the WeCom composer."""
        if self.client is None:
            raise PlatformError("企微 aibot 未连接", code="wecom_aibot_offline")
        media_type = "image" if mime.startswith("image/") else "file"
        uploaded = await self.client.upload_media(
            data, type=media_type, filename=filename
        )
        media_id = uploaded.get("media_id") if isinstance(uploaded, dict) else getattr(uploaded, "media_id", "")
        if not media_id:
            raise PlatformError("企微 aibot 媒体上传未返回 media_id", code="wecom_aibot_media")
        await self.client.send_media_message(chat_id, media_type, str(media_id))
        self.status = {**self.status, "replies": self.status.get("replies", 0) + 1}

    async def start(self) -> None:
        try:
            from wecom_aibot_sdk import WSClient
        except ImportError:
            self.status = {"state": "error", "detail": "wecom-aibot-sdk 未安装"}
            logger.warning("wecom-aibot-sdk not installed, WeCom runner disabled")
            return
        if not (self.settings.wecom_aibot_id and self.settings.wecom_aibot_secret):
            self.status = {"state": "error", "detail": "未配置 WECOM_AIBOT_ID / WECOM_AIBOT_SECRET"}
            logger.warning("WeCom aibot credentials missing, runner disabled")
            return
        self.client = WSClient(self.settings.wecom_aibot_id, self.settings.wecom_aibot_secret)
        for event in ("message.text", "message.image", "message.file", "message.voice", "message.mixed"):
            self.client.on(event, self._on_message)
        self.client.on("connected", lambda *_: self._set_state("running", "aibot WebSocket 已连接"))
        self.client.on(
            "disconnected",
            lambda reason=None, **_: self._set_state("reconnecting", f"aibot 连接断开: {reason or ''}".strip()),
        )
        self.client.on(
            "reconnecting",
            lambda attempt=0, **_: self._set_state("reconnecting", f"aibot 第 {attempt} 次重连"),
        )
        self.client.on("error", lambda error=None, **_: self._set_state("error", str(error)[:200]))
        adapter = self.adapters.get("wecom")
        binder = getattr(adapter, "bind_aibot", None)
        if binder is not None:
            binder(self)
        self.task = asyncio.create_task(self._connect(), name="wecom-ws")
        self.status = {"state": "starting", "detail": "企微 aibot 连接中", "replies": 0}

    def _set_state(self, state: str, detail: str) -> None:
        self.status = {**self.status, "state": state, "detail": detail}

    async def _connect(self) -> None:
        try:
            await self.client.connect()
        except Exception as exc:  # noqa: BLE001
            logger.exception("WeCom WebSocket connection failed")
            self._set_state("error", str(exc)[:200])

    async def _on_message(self, frame: Any) -> None:
        body = frame.get("body", {}) if isinstance(frame, dict) else {}
        msgid = str(body.get("msgid", ""))
        self.remember_frame(msgid, frame)
        msgtype = body.get("msgtype", "text")
        text = ""
        content_type = "text"
        attachments: list[AttachmentData] = []
        if msgtype == "text":
            text = (body.get("text") or {}).get("content", "")
        elif msgtype == "file":
            file_info = body.get("file") or {}
            text = file_info.get("file_name", "")
            content_type = "file"
            attachments.append(
                AttachmentData(
                    filename=file_info.get("file_name", "企微文件"),
                    source_url=file_info.get("url"),
                    aeskey=file_info.get("aeskey"),
                    platform_file_key=file_info.get("file_id") or file_info.get("url"),
                )
            )
        elif msgtype == "image":
            image_info = body.get("image") or {}
            text = "[图片]"
            content_type = "image"
            attachments.append(
                AttachmentData(
                    filename="wecom-image.jpg",
                    mime_type="image/jpeg",
                    source_url=image_info.get("url"),
                    aeskey=image_info.get("aeskey"),
                    platform_file_key=image_info.get("file_id") or image_info.get("url"),
                )
            )
        else:
            text = f"[{msgtype}]"
            content_type = msgtype
        sender = (body.get("from") or {}).get("userid", "")
        if sender:
            from .accounts import bind_wecom_aibot_operator

            async with self.db.session() as session:
                bound = await bind_wecom_aibot_operator(
                    session,
                    owner_id=self.owner_id,
                    sender_id=sender,
                    sender_name=str((body.get("from") or {}).get("name") or ""),
                    settings=self.settings,
                )
            if bound:
                logger.info("WeCom aibot bound operator identity %s", sender)
        await self._ingest(
            InboundMessageData(
                platform="wecom",
                platform_msg_id=str(body.get("msgid", "")),
                text=text,
                chat_id=body.get("chatid") or sender,
                chat_type="group" if body.get("chattype") == "group" else "direct",
                sender_platform_id=sender,
                sender_name=sender or "企微用户",
                content_type=content_type,
                attachments=attachments,
                raw=body,
            )
        )

    async def _ingest(self, data: InboundMessageData) -> None:
        async with self.db.session() as session:
            await ingest_message(
                session,
                settings=self.settings,
                bus=self.bus,
                owner_id=self.owner_id,
                adapters=self.adapters,
                data=data,
            )

    async def stop(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass


class RunnerManager:
    def __init__(self, settings: Settings, db: Database, adapters: AdapterRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.db = db
        self.adapters = adapters
        self.bus = bus
        self.runners: list[Any] = []
        self._started = False
        # Bot/app channels (Feishu WS, DingTalk Stream, WeCom aibot) connect with
        # the deployment's app credentials, so only one owner may own them.
        self.bot_owner_id: str | None = None

    async def start(self, owner_id: str) -> None:
        if not self.settings.enable_real_channels or self.settings.demo_mode:
            return
        if self._started:
            if self.bot_owner_id and self.bot_owner_id != owner_id:
                logger.info(
                    "Bot channels already bound to owner %s; not starting again for %s",
                    self.bot_owner_id,
                    owner_id,
                )
            return
        self._started = True
        self.bot_owner_id = owner_id
        if self.settings.feishu_enabled:
            runner = FeishuRunner(self.settings, self.db, self.adapters, self.bus, owner_id)
            await runner.start()
            self.runners.append(runner)
        if self.settings.dingtalk_enabled:
            runner = DingTalkRunner(self.settings, self.db, self.adapters, self.bus, owner_id)
            await runner.start()
            self.runners.append(runner)
        if self.settings.wecom_enabled:
            runner = WeComRunner(self.settings, self.db, self.adapters, self.bus, owner_id)
            await runner.start()
            self.runners.append(runner)

    async def stop(self) -> None:
        for runner in self.runners:
            await runner.stop()
        self.runners.clear()
        self._started = False
        self.bot_owner_id = None

    def info(self) -> dict[str, Any]:
        return {"bot_owner_id": self.bot_owner_id, "started": self._started}

    def status(self) -> dict[str, dict[str, Any]]:
        configured = {
            "feishu": bool(self.settings.feishu_enabled),
            "dingtalk": bool(self.settings.dingtalk_enabled),
            "wecom": bool(self.settings.wecom_enabled),
        }
        result: dict[str, dict[str, Any]] = {
            platform: {
                "state": "disabled" if not enabled else "configured",
                "detail": "未启用" if not enabled else "等待登录/启动",
            }
            for platform, enabled in configured.items()
        }
        for runner in self.runners:
            result[runner.platform] = runner.status
        return result
