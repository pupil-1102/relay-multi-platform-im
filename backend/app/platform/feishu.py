from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

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

BASE = "https://open.feishu.cn/open-apis"
MAX_FILE_BYTES = 30 * 1024 * 1024

# Feishu message types Relay does not render as rich content still deserve a
# readable placeholder; a bare "[interactive]" tells the operator nothing.
MESSAGE_TYPE_LABELS = {
    "folder": "[文件夹]",
    "media": "[音视频]",
    "audio": "[语音]",
    "video": "[视频]",
    "interactive": "[卡片消息]",
    "share_chat": "[群名片]",
    "share_user": "[个人名片]",
    "system": "[系统消息]",
    "location": "[位置]",
    "todo": "[任务]",
    "vote": "[投票]",
    "hongbao": "[红包]",
    "calendar": "[日程]",
    "calendar_share": "[日程分享]",
    "video_chat": "[视频会议]",
    "merge_forward": "[合并转发]",
}


def message_type_label(msg_type: str) -> str:
    """Human label for a message type Relay cannot unpack further."""
    return MESSAGE_TYPE_LABELS.get(msg_type, f"[{msg_type}]")


class FeishuAdapter(PlatformAdapter):
    platform = "feishu"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tenant_token = ""
        self._tenant_expires = 0.0
        self._user_tokens: dict[str, tuple[str, float]] = {}
        self._chat_names: dict[str, tuple[str, float]] = {}
        self._user_names: dict[str, tuple[str, float]] = {}

    async def get_user_name(self, account: AccountContext, open_id: str) -> str:
        if not open_id:
            return ""
        cached = self._user_names.get(open_id)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        try:
            headers = await self._headers(account, prefer_user=False)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BASE}/contact/v3/users/{open_id}",
                    headers=headers,
                    params={"user_id_type": "open_id"},
                )
            data = self._check(resp)
            user = data.get("user") or {}
            name = str(user.get("name") or user.get("en_name") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        self._user_names[open_id] = (name, time.monotonic() + 600)
        return name

    async def get_chat_name(self, account: AccountContext, chat_id: str) -> str:
        """Resolve a Feishu group/direct chat display name; empty when unavailable."""
        if not chat_id:
            return ""
        cached = self._chat_names.get(chat_id)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        try:
            headers = await self._headers(account, prefer_user=False)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{BASE}/im/v1/chats/{chat_id}", headers=headers)
            data = self._check(resp)
            name = str(data.get("name") or data.get("chat_name") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        self._chat_names[chat_id] = (name, time.monotonic() + 600)
        return name

    async def _tenant_access_token(self) -> str:
        if self._tenant_token and time.monotonic() < self._tenant_expires:
            return self._tenant_token
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret},
            )
        data = self._json(resp)
        if data.get("code") != 0:
            raise PlatformError(f"飞书 tenant token 获取失败: {data.get('msg')}", code="feishu_token")
        self._tenant_token = data["tenant_access_token"]
        self._tenant_expires = time.monotonic() + max(int(data.get("expire", 7200)) - 300, 60)
        return self._tenant_token

    async def _headers(self, account: AccountContext, *, prefer_user: bool = True) -> dict[str, str]:
        token = ""
        if prefer_user and account.access_token:
            token = account.access_token
        if not token:
            token = await self._tenant_access_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except ValueError as exc:
            raise PlatformError(f"飞书返回非 JSON ({resp.status_code})", code="feishu_bad_response") from exc

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        data = self._json(resp)
        if resp.status_code >= 400 or data.get("code", 0) != 0:
            raise PlatformError(
                f"飞书接口失败: {data.get('msg') or resp.text[:200]}",
                code=f"feishu_{data.get('code', resp.status_code)}",
                retryable=resp.status_code >= 500 or data.get("code") in {99991400, 99991401},
            )
        return data.get("data", data)

    def _check_file_upload(self, resp: httpx.Response) -> dict[str, Any]:
        """Turn Feishu's 99991679 into an actionable message.

        Uploading a file with a user token needs the *user* identity scope
        ``im:resource:upload``; without it Feishu answers 99991679 with a long
        English blob that tells the operator nothing.
        """
        try:
            return self._check(resp)
        except PlatformError as exc:
            if exc.code == "feishu_99991679":
                raise PlatformError(
                    "飞书拒绝了以本人身份上传文件：应用缺少用户授权 im:resource:upload。"
                    "请在飞书开放平台给应用勾选该权限（用户身份），重新授权后再试。",
                    code="feishu_user_scope_missing",
                ) from exc
            raise

    async def send_text(self, account: AccountContext, to_platform_id: str, text: str) -> SendResult:
        # Group/direct chats speak as the bot; open_id targets keep user identity
        # when available so personal replies still look like the operator.
        headers = await self._headers(account, prefer_user=not to_platform_id.startswith("oc_"))
        receive_type = "chat_id" if to_platform_id.startswith("oc_") else "open_id"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/im/v1/messages",
                params={"receive_id_type": receive_type},
                headers=headers,
                json={
                    "receive_id": to_platform_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("message_id", ""), raw=data)

    async def reply_text(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        headers = await self._headers(account)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/im/v1/messages/{message.platform_msg_id}/reply",
                headers=headers,
                json={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
            )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("message_id", ""), raw=data)

    async def send_text_as_user(
        self, account: AccountContext, to_platform_id: str, text: str
    ) -> SendResult:
        """Send as the authenticated operator (user_access_token), not the bot."""
        if not account.access_token:
            raise PlatformError("以本人身份发送需要 user_access_token", code="feishu_user_token_missing")
        headers = await self._headers(account, prefer_user=True)
        if to_platform_id.startswith("oc_"):
            receive_type = "chat_id"
        elif "@" in to_platform_id:
            receive_type = "email"
        else:
            receive_type = "open_id"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/im/v1/messages",
                params={"receive_id_type": receive_type},
                headers=headers,
                json={
                    "receive_id": to_platform_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("message_id", ""), raw=data)

    async def resolve_open_id_by_mobile(
        self, account: AccountContext, mobile: str
    ) -> str:
        """Resolve a Feishu directory mobile to its open_id.

        External contacts are generally outside the tenant directory and will
        not resolve here; callers should ask for their open_id/email instead.
        """
        return await self._resolve_open_id(account, "mobiles", mobile)

    async def resolve_open_id_by_email(
        self, account: AccountContext, email: str
    ) -> str:
        """Resolve a Feishu directory email to its open_id."""
        return await self._resolve_open_id(account, "emails", email)

    async def download_image(
        self, account: AccountContext, message_id: str, image_key: str
    ) -> tuple[bytes, str, str]:
        """Download an inbound Feishu image resource from its message."""
        return await self.download_message_resource(account, message_id, image_key, "image")

    async def download_message_resource(
        self,
        account: AccountContext,
        message_id: str,
        file_key: str,
        resource_type: str,
        *,
        as_user: bool = False,
    ) -> tuple[bytes, str, str]:
        """Download a message resource (image/file) from Feishu."""
        from urllib.parse import quote

        headers = await self._headers(account, prefer_user=as_user)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE}/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}",
                params={"type": resource_type},
                headers={"Authorization": headers["Authorization"]},
            )
        if resp.status_code >= 400:
            raise PlatformError(
                f"飞书资源下载失败: {resp.status_code} {resp.text[:200]}",
                code="feishu_resource_download",
                retryable=resp.status_code >= 500,
            )
        mime = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
        data = resp.content
        if mime in {"application/octet-stream", "binary/octet-stream"}:
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif data.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif data.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                mime = "image/webp"
        ext = {
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/png": "png",
        }.get(mime, "bin")
        return data, mime, f"feishu-{resource_type}-{file_key[:12]}.{ext}"

    async def list_chat_members(
        self, account: AccountContext, chat_id: str
    ) -> list[dict[str, str]]:
        headers = await self._headers(account, prefer_user=False)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{BASE}/im/v1/chats/{chat_id}/members",
                params={"member_id_type": "open_id", "page_size": 100},
                headers=headers,
            )
        data = self._check(resp)
        items = []
        for item in data.get("items") or []:
            member_id = str(item.get("member_id") or "")
            name = str(item.get("name") or "").strip()
            if member_id and member_id.startswith("ou_"):
                items.append({"id": member_id, "name": name or "飞书成员"})
        return items

    async def list_user_chats(self, account: AccountContext) -> list[dict[str, Any]]:
        headers = await self._headers(account, prefer_user=True)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE}/im/v1/chats",
                params={"page_size": 100, "user_id_type": "open_id"},
                headers=headers,
            )
        data = self._check(resp)
        return list(data.get("items") or [])

    async def list_user_chat_messages(
        self, account: AccountContext, chat_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        headers = await self._headers(account, prefer_user=True)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{BASE}/im/v1/messages",
                params={
                    "container_id_type": "chat",
                    "container_id": chat_id,
                    "sort_type": "ByCreateTimeDesc",
                    "page_size": min(max(page_size, 1), 50),
                },
                headers=headers,
            )
        data = self._check(resp)
        return list(data.get("items") or [])

    async def search_user_p2p_chats(
        self, account: AccountContext, days: int = 7
    ) -> list[dict[str, Any]]:
        """Discover personal p2p chat IDs through user message search.

        The chat-list API intentionally excludes p2p conversations. Feishu's
        message search returns ``chat_id`` + ``is_p2p_chat`` for the user's
        visible messages, which gives us the missing IDs.
        """
        headers = await self._headers(account, prefer_user=True)
        body = {"filter": {"chat_type": "p2p"}}
        chats: dict[str, dict[str, Any]] = {}
        page_token = ""
        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(20):
                params = {"page_size": 30, "user_id_type": "open_id"}
                if page_token:
                    params["page_token"] = page_token
                resp = await client.post(
                    f"{BASE}/im/v1/messages/search",
                    params=params,
                    headers=headers,
                    json=body,
                )
                data = self._check(resp)
                for item in data.get("items") or []:
                    meta = item.get("meta_data") or {}
                    chat_id = str(meta.get("chat_id") or item.get("chat_id") or "")
                    if not chat_id:
                        continue
                    display = str(item.get("display_info") or "").strip()
                    display_name = display.split("\n", 1)[0].strip() if display else ""
                    if len(display_name) > 40:
                        display_name = display_name[:40]
                    chats[chat_id] = {
                        "chat_id": chat_id,
                        "chat_mode": "p2p",
                        "name": display_name,
                    }
                if not data.get("has_more"):
                    break
                page_token = str(data.get("page_token") or "")
                if not page_token:
                    break
        return list(chats.values())

    async def _resolve_open_id(
        self, account: AccountContext, field: str, value: str
    ) -> str:
        headers = await self._headers(account, prefer_user=False)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/contact/v3/users/batch_get_id",
                params={"user_id_type": "open_id"},
                headers=headers,
                json={field: [value]},
            )
        data = self._check(resp)
        for item in data.get("user_list") or []:
            if item.get("user_id"):
                return str(item["user_id"])
        return ""

    async def list_directory_users(self, account: AccountContext) -> list[dict[str, Any]]:
        """List visible Feishu directory users (requires contact data scope)."""
        headers = await self._headers(account, prefer_user=False)
        users: list[dict[str, Any]] = []
        page_token = ""
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(10):
                params: dict[str, Any] = {
                    "department_id": "0",
                    "page_size": 50,
                    "user_id_type": "open_id",
                }
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(f"{BASE}/contact/v3/users", headers=headers, params=params)
                data = self._check(resp)
                users.extend(data.get("items") or [])
                if not data.get("has_more"):
                    break
                page_token = data.get("page_token") or ""
                if not page_token:
                    break
        return users

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
            raise CapabilityError("飞书单文件不能超过 30MB")
        if as_user and not account.access_token:
            raise PlatformError(
                "以本人身份发送文件需要先完成飞书授权", code="feishu_user_token_missing"
            )
        # The bot is not a member of the operator's personal chats, so a
        # tenant-token send there fails with 230002 "Bot/User can NOT be out of
        # the chat". Direct chats therefore have to speak as the operator.
        headers = await self._headers(account, prefer_user=as_user)
        caption: SendResult | None = None
        async with httpx.AsyncClient(timeout=120) as client:
            upload = await client.post(
                f"{BASE}/im/v1/files",
                headers={"Authorization": headers["Authorization"]},
                data={"file_type": "stream", "file_name": file.filename},
                files={"file": (file.filename, file.data, file.mime_type or "application/octet-stream")},
            )
            upload_data = self._check_file_upload(upload)
            file_key = upload_data.get("file_key")
            if not file_key:
                raise PlatformError("飞书文件上传未返回 file_key", code="feishu_file_key")
            if text:
                # Send the caption only once the upload worked, otherwise a
                # caller that retries with the bot identity repeats the text.
                if as_user:
                    caption = await self.send_text_as_user(account, to_platform_id, text)
                else:
                    caption = await self.send_text(account, to_platform_id, text)
            resp = await client.post(
                f"{BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id" if to_platform_id.startswith("oc_") else "open_id"},
                headers=headers,
                json={
                    "receive_id": to_platform_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
                },
            )
        data = self._check(resp)
        raw: dict[str, Any] = dict(data) if isinstance(data, dict) else {"data": data}
        if caption is not None and caption.platform_msg_id:
            # The caption is a second platform message; without this marker the
            # user-token mirror ingests it again as a duplicate bubble.
            raw["caption_message_id"] = caption.platform_msg_id
        return SendResult(platform_msg_id=data.get("message_id", ""), raw=raw)

    async def create_task(
        self, account: AccountContext, draft: TaskDraft, assignee: AccountContext | None = None
    ) -> PlatformTaskRef:
        headers = await self._headers(account)
        members: list[dict[str, Any]] = []
        if assignee and assignee.platform_user_id:
            members.append({"id": assignee.platform_user_id, "type": "user", "role": "assignee"})
        body: dict[str, Any] = {
            "summary": draft.title[:3000],
            "description": (draft.description or "")[:3000],
            "extra": json.dumps({"relay_task_id": draft.unified_id}, ensure_ascii=False),
            "members": members,
        }
        if draft.due_at:
            body["due"] = {"timestamp": str(int(draft.due_at.timestamp() * 1000)), "is_all_day": False}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/task/v2/tasks",
                params={"user_id_type": "open_id"},
                headers=headers,
                json=body,
            )
        data = self._check(resp)
        task = data.get("task", {})
        return PlatformTaskRef(
            platform_task_id=task.get("guid", ""),
            platform_status="done" if task.get("completed_at") not in (None, "", "0") else "open",
            meta={"raw": task},
        )

    async def update_task_status(self, account: AccountContext, link: Any, status: str, token: str) -> None:
        headers = await self._headers(account)
        completed_at = str(int(datetime.now(UTC).timestamp() * 1000)) if status == "done" else "0"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.patch(
                f"{BASE}/task/v2/tasks/{link.platform_task_id}",
                params={"user_id_type": "open_id"},
                headers=headers,
                json={"task": {"completed_at": completed_at}, "update_fields": ["completed_at"]},
            )
        self._check(resp)

    async def health_check(self) -> None:
        """Raise a PlatformError when app credentials cannot mint a tenant token."""
        await self._tenant_access_token()

    async def subscribe_user_tasks(self, account: AccountContext) -> bool:
        """Ask Feishu to deliver task updates visible to this user.

        Required before ``task.task.update_user_access_v2`` events arrive. The
        subscription is idempotent; calling it again on reconnect is harmless.
        """
        if not account.access_token:
            raise PlatformError("飞书用户任务订阅需要 user_access_token", code="feishu_user_token_missing")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/task/v2/task_v2/task_subscription",
                params={"user_id_type": "open_id"},
                headers={
                    "Authorization": f"Bearer {account.access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={},
            )
        data = self._check(resp)
        return bool(data is not None)

    async def fetch_task(self, account: AccountContext, link: Any) -> PlatformTaskState:
        headers = await self._headers(account)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{BASE}/task/v2/tasks/{link.platform_task_id}",
                params={"user_id_type": "open_id"},
                headers=headers,
            )
        data = self._check(resp)
        task = data.get("task", {})
        status = "done" if task.get("completed_at") not in (None, "", "0") else "open"
        return PlatformTaskState(
            status=status,
            raw_status=str(task.get("completed_at", "")),
            updated_at=None,
            title=task.get("summary"),
        )

    async def download_file(self, account: AccountContext, file: Any) -> bytes:
        headers = await self._headers(account)
        message_id = file.message_id if hasattr(file, "message_id") else file.get("message_id")
        file_key = file.platform_file_key if hasattr(file, "platform_file_key") else file.get("platform_file_key")
        if not message_id or not file_key:
            raise PlatformError("飞书文件缺少 message_id 或 file_key", code="feishu_file_ref")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{BASE}/im/v1/messages/{message_id}/resources/{file_key}",
                params={"type": "file"},
                headers={"Authorization": headers["Authorization"]},
            )
        if resp.status_code >= 400:
            raise PlatformError(f"飞书文件下载失败 HTTP {resp.status_code}", code="feishu_file_download")
        return resp.content
