from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import Settings
from .base import (
    AccountContext,
    CapabilityError,
    MessageRef,
    OutboundFile,
    PlatformAdapter,
    PlatformError,
    PlatformTaskRef,
    PlatformTaskState,
    SendResult,
    TaskDraft,
)

BASE = "https://qyapi.weixin.qq.com/cgi-bin"
MAX_FILE_BYTES = 20 * 1024 * 1024


class WeComAdapter(PlatformAdapter):
    platform = "wecom"
    supports_task_creation = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token = ""
        self._expires = 0.0
        self._ticket = ""
        self._ticket_expires = 0.0
        # Set by WeComRunner once the aibot WebSocket is live. Aibot chats are
        # a different conversation from application messages, so replies must
        # go back through the original frame instead of /message/send.
        self._aibot: Any = None

    def bind_aibot(self, channel: Any) -> None:
        self._aibot = channel

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires:
            return self._token
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{BASE}/gettoken",
                params={"corpid": self.settings.wecom_corp_id, "corpsecret": self.settings.wecom_secret},
            )
        data = self._json(resp)
        if data.get("errcode") != 0:
            raise PlatformError(f"企微 token 获取失败: {data.get('errmsg')}", code="wecom_token")
        self._token = data["access_token"]
        self._expires = time.monotonic() + max(int(data.get("expires_in", 7200)) - 300, 60)
        return self._token

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except ValueError as exc:
            raise PlatformError(f"企微返回非 JSON ({resp.status_code})", code="wecom_bad_response") from exc

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        data = self._json(resp)
        if resp.status_code >= 400 or data.get("errcode", 0) != 0:
            raise PlatformError(
                f"企微接口失败: {data.get('errmsg') or resp.text[:200]}",
                code=f"wecom_{data.get('errcode', resp.status_code)}",
                retryable=resp.status_code >= 500,
            )
        return data

    async def send_text(self, account: AccountContext, to_platform_id: str, text: str) -> SendResult:
        if self.settings.wecom_aibot_only:
            if self._aibot is None:
                raise CapabilityError("企微 aibot 尚未连接")
            await self._aibot.send_to_chat(to_platform_id, text)
            return SendResult(platform_msg_id="", raw={"channel": "aibot"})
        token = await self._access_token()
        payload: dict[str, Any] = {
            "msgtype": "text",
            "agentid": int(self.settings.wecom_agent_id or 0),
            "text": {"content": text},
        }
        payload["touser"] = to_platform_id
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{BASE}/message/send", params={"access_token": token}, json=payload)
        data = self._check(resp)
        return SendResult(platform_msg_id=str(data.get("msgid", "")), raw=data)

    async def reply_text(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        # Aibot conversations must be answered inside the same WebSocket frame,
        # otherwise the text lands in the application chat and the operator sees
        # a "reply" in a different conversation.
        channel = self._aibot
        if channel is not None and message.platform_msg_id:
            if channel.has_frame(message.platform_msg_id):
                await channel.reply_in_conversation(message.platform_msg_id, text)
                return SendResult(platform_msg_id="", raw={"channel": "aibot"})
            if (message.source_raw or {}).get("aibotid"):
                if self.settings.wecom_aibot_only and hasattr(channel, "send_to_chat"):
                    raw = message.source_raw or {}
                    chat_id = str(raw.get("chatid") or message.sender_platform_id or "")
                    if chat_id:
                        await channel.send_to_chat(chat_id, text)
                        return SendResult(platform_msg_id="", raw={"channel": "aibot_proactive"})
                raise PlatformError(
                    "企微 aibot 消息帧已失效（Relay 可能重启过），请在企微里重新发一条消息再回复",
                    code="wecom_aibot_frame_missing",
                )
        # WeCom has no native quote-reply API for self-built apps. The product
        # contract is "deliver a reply", so use the reliable app message channel.
        token = await self._access_token()
        payload: dict[str, Any] = {
            "msgtype": "text",
            "agentid": int(self.settings.wecom_agent_id or 0),
            "text": {"content": text},
        }
        if message.chat_type == "group" and message.chat_id:
            payload["chatid"] = message.chat_id
        else:
            payload["touser"] = message.sender_platform_id or ""
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{BASE}/message/send", params={"access_token": token}, json=payload)
        data = self._check(resp)
        return SendResult(platform_msg_id=str(data.get("msgid", "")), raw=data)

    async def create_task(
        self, account: AccountContext, draft: TaskDraft, assignee: AccountContext | None = None
    ) -> PlatformTaskRef:
        raise CapabilityError(
            "企业微信服务端不支持创建待办。请使用企微客户端 H5 的 JS-SDK 创建，"
            "并把返回的 todo_id 回传到统一任务。"
        )

    async def send_file(
        self,
        account: AccountContext,
        to_platform_id: str,
        file: OutboundFile,
        text: str | None = None,
        *,
        chat_id: str | None = None,
        as_user: bool = False,
    ) -> SendResult:
        if not file.data:
            raise CapabilityError("不能发送空文件")
        if len(file.data) > MAX_FILE_BYTES:
            raise CapabilityError("企微单文件不能超过 20MB")
        if text:
            await self.send_text(account, to_platform_id, text)
        if self.settings.wecom_aibot_only:
            if self._aibot is None:
                raise CapabilityError("企微 aibot 尚未连接")
            await self._aibot.send_media(
                to_platform_id,
                file.data,
                file.mime_type or "application/octet-stream",
                file.filename,
            )
            return SendResult(platform_msg_id="", raw={"channel": "aibot"})
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=120) as client:
            upload = await client.post(
                f"{BASE}/media/upload",
                params={"access_token": token, "type": "file"},
                files={"media": (file.filename, file.data, file.mime_type or "application/octet-stream")},
            )
            upload_data = self._json(upload)
            media_id = upload_data.get("media_id")
            if upload.status_code >= 400 or not media_id:
                raise PlatformError(
                    f"企微文件上传失败: {upload_data.get('errmsg') or upload.status_code}",
                    code="wecom_file_upload",
                )
            payload: dict[str, Any] = {
                "msgtype": "file",
                "agentid": int(self.settings.wecom_agent_id or 0),
                "file": {"media_id": media_id},
            }
            if chat_id:
                payload["chatid"] = chat_id
            else:
                payload["touser"] = to_platform_id
            resp = await client.post(f"{BASE}/message/send", params={"access_token": token}, json=payload)
        data = self._check(resp)
        return SendResult(platform_msg_id=str(data.get("msgid", "")), raw=data)

    async def update_task_status(self, account: AccountContext, link: Any, status: str, token: str) -> None:
        access_token = await self._access_token()
        todo_status = 0 if status == "done" else 1
        body = {
            "todo_id": link.platform_task_id,
            "status": todo_status,
            "attendees": [{"userid": account.platform_user_id, "status": todo_status}],
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{BASE}/todo/update", params={"access_token": access_token}, json=body)
        self._check(resp)

    async def fetch_task(self, account: AccountContext, link: Any) -> PlatformTaskState:
        access_token = await self._access_token()
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/todo/get",
                params={"access_token": access_token},
                json={"todo_id_list": [link.platform_task_id]},
            )
        data = self._check(resp)
        items = data.get("data_list") or []
        item = items[0] if items else {}
        todo_status = item.get("todo_status")
        status = "done" if todo_status == 0 else ("in_progress" if todo_status == 1 else "open")
        return PlatformTaskState(
            status=status,
            raw_status=str(todo_status),
            updated_at=None,
            title=item.get("content"),
        )

    async def jsapi_config(self, url: str) -> dict[str, Any]:
        token = await self._access_token()
        if not self._ticket or time.monotonic() >= self._ticket_expires:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{BASE}/get_jsapi_ticket", params={"access_token": token})
            data = self._json(resp)
            if data.get("errcode") != 0:
                raise PlatformError(f"企微 jsapi_ticket 获取失败: {data.get('errmsg')}", code="wecom_ticket")
            self._ticket = data["ticket"]
            self._ticket_expires = time.monotonic() + max(int(data.get("expires_in", 7200)) - 300, 60)
        nonce = secrets.token_hex(8)
        timestamp = int(time.time())
        raw = f"jsapi_ticket={self._ticket}&noncestr={nonce}&timestamp={timestamp}&url={url}"
        signature = hashlib.sha1(raw.encode()).hexdigest()
        return {
            "corp_id": self.settings.wecom_corp_id,
            "agent_id": self.settings.wecom_agent_id,
            "timestamp": timestamp,
            "nonce_str": nonce,
            "signature": signature,
            "url": url,
        }

    async def list_directory_users(self, account: AccountContext) -> list[dict[str, Any]]:
        """List WeCom directory members with the app/contact secret."""
        secret = self.settings.wecom_contact_secret or self.settings.wecom_secret
        if not (self.settings.wecom_corp_id and secret):
            raise CapabilityError(
                "企业微信通讯录未配置：需要 WECOM_CORP_ID 和 "
                "WECOM_SECRET/WECOM_CONTACT_SECRET"
            )
        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.get(
                f"{BASE}/gettoken",
                params={
                    "corpid": self.settings.wecom_corp_id,
                    "corpsecret": secret,
                },
            )
            token_data = self._json(token_resp)
            token = token_data.get("access_token")
            if token_data.get("errcode") != 0 or not token:
                raise PlatformError(
                    f"企业微信通讯录 token 失败: {token_data.get('errmsg')}",
                    code="wecom_contact_token",
                )
            dept_resp = await client.get(
                f"{BASE}/department/list", params={"access_token": token}
            )
            dept_data = self._json(dept_resp)
            if dept_data.get("errcode", 0) != 0:
                raise PlatformError(
                    f"企业微信部门读取失败: {dept_data.get('errmsg')}",
                    code="wecom_department_scope",
                )
            user_resp = await client.get(
                f"{BASE}/user/list",
                params={
                    "access_token": token,
                    "department_id": 1,
                    "fetch_child": 1,
                },
            )
            user_data = self._json(user_resp)
            if user_data.get("errcode", 0) != 0:
                raise PlatformError(
                    f"企业微信成员读取失败: {user_data.get('errmsg')}",
                    code="wecom_member_scope",
                )
        users: list[dict[str, Any]] = []
        for item in user_data.get("userlist") or []:
            user_id = str(item.get("userid") or "").strip()
            if not user_id:
                continue
            users.append(
                {
                    "open_id": user_id,
                    "name": item.get("name") or "企微联系人",
                    "email": item.get("email") or "",
                    "title": item.get("position") or "",
                    "union_id": item.get("unionid") or "",
                    "departments": item.get("department") or [],
                }
            )
        return users

    async def download_file(self, account: AccountContext, file: Any) -> bytes:
        url = file.source_url if hasattr(file, "source_url") else file.get("source_url")
        aeskey = file.aeskey if hasattr(file, "aeskey") else file.get("aeskey")
        if not url:
            raise PlatformError("企微文件缺少下载 URL", code="wecom_file_url")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise PlatformError(f"企微文件下载 HTTP {resp.status_code}", code="wecom_file_download")
        data = resp.content
        if aeskey:
            try:
                from wecom_aibot_sdk.crypto import decrypt_file
            except ImportError as exc:
                raise PlatformError("缺少 wecom-aibot-sdk，无法解密企微文件", code="wecom_sdk_missing") from exc
            try:
                data = decrypt_file(data, aeskey)
            except Exception as exc:  # noqa: BLE001
                raise PlatformError(f"企微文件解密失败: {exc}", code="wecom_decrypt") from exc
        return data
