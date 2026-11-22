from __future__ import annotations

import asyncio
import json
import os
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

BASE = "https://api.dingtalk.com"
MAX_FILE_BYTES = 20 * 1024 * 1024


class DingTalkAdapter(PlatformAdapter):
    platform = "dingtalk"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token = ""
        self._expires = 0.0
        self._legacy_token = ""
        self._legacy_expires = 0.0

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires:
            return self._token
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/v1.0/oauth2/accessToken",
                json={"appKey": self.settings.dingtalk_client_id, "appSecret": self.settings.dingtalk_client_secret},
            )
        data = self._json(resp)
        if "accessToken" not in data:
            raise PlatformError(f"钉钉 token 获取失败: {data}", code="dingtalk_token")
        self._token = data["accessToken"]
        self._expires = time.monotonic() + max(int(data.get("expireIn", 7200)) - 300, 60)
        return self._token

    async def _headers(self) -> dict[str, str]:
        return {
            "x-acs-dingtalk-access-token": await self._access_token(),
            "Content-Type": "application/json",
        }

    async def _legacy_access_token(self) -> str:
        """Old media/upload endpoint still uses the oapi access_token."""
        if self._legacy_token and time.monotonic() < self._legacy_expires:
            return self._legacy_token
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={
                    "appkey": self.settings.dingtalk_client_id,
                    "appsecret": self.settings.dingtalk_client_secret,
                },
            )
        data = self._json(resp)
        if not data.get("access_token"):
            raise PlatformError(f"钉钉 legacy token 获取失败: {data.get('errmsg')}", code="dingtalk_legacy_token")
        self._legacy_token = data["access_token"]
        self._legacy_expires = time.monotonic() + max(int(data.get("expires_in", 7200)) - 300, 60)
        return self._legacy_token

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except ValueError as exc:
            raise PlatformError(f"钉钉返回非 JSON ({resp.status_code})", code="dingtalk_bad_response") from exc

    def _check(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise PlatformError(
                f"钉钉接口失败 HTTP {resp.status_code}: {detail}",
                code=f"dingtalk_{resp.status_code}",
                retryable=resp.status_code >= 500,
            )
        return self._json(resp)

    @property
    def _robot_code(self) -> str:
        return self.settings.dingtalk_robot_code or self.settings.dingtalk_client_id

    async def send_text(
        self,
        account: AccountContext,
        to_platform_id: str,
        text: str,
        mentions: list[dict[str, str]] | None = None,
    ) -> SendResult:
        headers = await self._headers()
        if (
            mentions
            and (to_platform_id.startswith("cid") or to_platform_id.startswith("$"))
            and self.settings.dingtalk_dws_command
        ):
            return await self._send_group_with_mentions(to_platform_id, text, mentions)
        if to_platform_id.startswith("cid") or to_platform_id.startswith("$"):
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{BASE}/v1.0/robot/groupMessages/send",
                    headers=headers,
                    json={
                        "robotCode": self._robot_code,
                        "openConversationId": to_platform_id,
                        "msgKey": "sampleText",
                        "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                    },
                )
            data = self._check(resp)
            return SendResult(platform_msg_id=data.get("processQueryKey", ""), raw=data)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/v1.0/robot/oToMessages/batchSend",
                headers=headers,
                json={
                    "robotCode": self._robot_code,
                    "userIds": [to_platform_id],
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                },
            )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("processQueryKey", ""), raw=data)

    async def send_text_as_user(
        self, account: AccountContext, to_platform_id: str, text: str
    ) -> SendResult:
        """Send a personal-chat message as the signed-in DingTalk user."""
        if not to_platform_id.startswith("cid"):
            raise CapabilityError("钉钉用户态发送需要真实的会话 ID")
        command = self.settings.dingtalk_dws_command
        args = [
            command,
            "chat",
            "+messages-send",
            "--group",
            to_platform_id,
            "--text",
            text,
            "--format",
            "json",
            "--yes",
        ]
        env = os.environ.copy()
        if self.settings.dingtalk_dws_config_dir:
            env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except FileNotFoundError as exc:
            raise CapabilityError(f"找不到 dws 命令：{command}") from exc
        except asyncio.TimeoutError as exc:
            raise PlatformError("钉钉用户态发送超时", code="dingtalk_dws_timeout", retryable=True) from exc
        if proc.returncode != 0:
            raise PlatformError(
                f"dws 用户态发送失败: {stderr.decode('utf-8', 'ignore')[:400]}",
                code="dingtalk_dws_send",
            )
        raw_text = stdout.decode("utf-8", "ignore").strip()
        try:
            payload = json.loads(raw_text.splitlines()[-1]) if raw_text else {}
        except (ValueError, IndexError):
            payload = {"raw": raw_text}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        inner = result.get("result") if isinstance(result.get("result"), dict) else {}
        receipt = payload.get("sendReceipt") if isinstance(payload.get("sendReceipt"), dict) else {}
        platform_id = str(
            payload.get("messageId")
            or result.get("messageId")
            or inner.get("messageId")
            or receipt.get("openTaskId")
            or ""
        )
        return SendResult(platform_msg_id=platform_id, raw=payload)

    async def _send_group_with_mentions(
        self, chat_id: str, text: str, mentions: list[dict[str, str]]
    ) -> SendResult:
        open_ids: list[str] = []
        mention_names: dict[str, str] = {}
        for item in mentions:
            candidate = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            resolved = ""
            if candidate and not candidate.isdigit():
                resolved = candidate
            elif name:
                payload = await self._dws_json(
                    ["contact", "user", "search", "--query", name, "--format", "json"]
                )
                for row in payload.get("result") or []:
                    if str(row.get("userId") or "") == candidate and row.get("openDingTalkId"):
                        resolved = str(row["openDingTalkId"])
                        break
            if resolved:
                open_ids.append(resolved)
                if name:
                    mention_names[name] = resolved
        open_ids = [item for item in open_ids if item]
        if not open_ids:
            raise CapabilityError("无法解析钉钉 @ 成员的 openDingTalkId")
        wire_text = text
        for name, open_id in mention_names.items():
            wire_text = wire_text.replace(f"@{name}", f"<@{open_id}>")
        command = self.settings.dingtalk_dws_command
        args = [
            command,
            "chat",
            "+messages-send",
            "--group",
            chat_id,
            "--text",
            wire_text,
            "--at-open-dingtalk-ids",
            ",".join(open_ids),
            "--format",
            "json",
            "--yes",
        ]
        env = os.environ.copy()
        if self.settings.dingtalk_dws_config_dir:
            env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except FileNotFoundError as exc:
            raise CapabilityError(f"找不到 dws 命令：{command}") from exc
        except asyncio.TimeoutError as exc:
            raise PlatformError("钉钉 @ 消息发送超时", code="dingtalk_dws_at_timeout", retryable=True) from exc
        if proc.returncode != 0:
            raise PlatformError(
                f"钉钉 @ 消息发送失败: {stderr.decode('utf-8', 'ignore')[:400]}",
                code="dingtalk_dws_at",
            )
        raw_text = stdout.decode("utf-8", "ignore").strip()
        try:
            payload = json.loads(raw_text.splitlines()[-1]) if raw_text else {}
        except (ValueError, IndexError):
            payload = {"raw": raw_text}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        receipt = payload.get("sendReceipt") if isinstance(payload.get("sendReceipt"), dict) else {}
        platform_id = str(
            payload.get("messageId")
            or result.get("messageId")
            or receipt.get("openTaskId")
            or ""
        )
        return SendResult(platform_msg_id=platform_id, raw=payload)

    async def _dws_json(self, args: list[str]) -> dict[str, Any]:
        command = self.settings.dingtalk_dws_command
        env = os.environ.copy()
        if self.settings.dingtalk_dws_config_dir:
            env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            raise PlatformError(
                f"DWS 查询失败: {stderr.decode('utf-8', 'ignore')[:300]}",
                code="dingtalk_dws_query",
            )
        try:
            return json.loads(stdout.decode("utf-8", "ignore").strip() or "{}")
        except ValueError as exc:
            raise PlatformError("DWS 返回非 JSON", code="dingtalk_dws_bad_json") from exc

    async def reply_text(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        if message.chat_type == "group" and message.chat_id and message.sender_platform_id:
            return await self._reply_group_quote(message, text)
        return await self._reply_one_to_one(account, message, text)

    async def _reply_group_quote(self, message: MessageRef, text: str) -> SendResult:
        """Native DingTalk quote reply: group chats + Markdown only."""
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/v1.0/robot/groupMessages/send",
                headers=headers,
                json={
                    "robotCode": self._robot_code,
                    "openConversationId": message.chat_id,
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps(
                        {"title": "任务回复", "text": text},
                        ensure_ascii=False,
                    ),
                    "referenceOpenMessageId": message.platform_msg_id,
                    "srcMsgSendOpenDingTalkId": message.sender_platform_id,
                },
            )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("processQueryKey", ""), raw=data)

    async def _reply_one_to_one(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        """DingTalk bots have no native 1:1 quote parameter.

        The agreed product behaviour is to reply as the signed-in user through the
        official ``dws`` user CLI. It is opt-in because it needs a logged-in CLI
        profile on the host. If it is not configured we fail loudly instead of
        silently degrading to a plain message.
        """
        if not self.settings.dingtalk_user_reply_enabled:
            raise CapabilityError(
                "钉钉单聊引用回复需要用户态通道：请启用 DINGTALK_USER_REPLY_ENABLED "
                "并配置可用的 dws 登录态（对应“用户身份回复”方案）。"
            )
        command = self.settings.dingtalk_dws_command
        args = [
            command,
            "chat",
            "+messages-reply",
            "--group",
            message.chat_id or message.platform_msg_id,
            "--message-id",
            message.platform_msg_id,
            "--content",
            text,
            "--ref-sender",
            message.sender_platform_id or "",
            "--format",
            "json",
            "--yes",
        ]
        env = os.environ.copy()
        if self.settings.dingtalk_dws_config_dir:
            env["DWS_CONFIG_DIR"] = self.settings.dingtalk_dws_config_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError as exc:
            raise CapabilityError(f"找不到 dws 命令：{command}") from exc
        except asyncio.TimeoutError as exc:
            raise PlatformError("dws 用户态回复超时", code="dingtalk_dws_timeout", retryable=True) from exc
        if proc.returncode != 0:
            raise PlatformError(
                f"dws 回复失败: {stderr.decode('utf-8', 'ignore')[:300]}",
                code="dingtalk_dws_reply",
            )
        raw_text = stdout.decode("utf-8", "ignore").strip()
        platform_id = ""
        try:
            payload = json.loads(raw_text.splitlines()[-1]) if raw_text else {}
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            platform_id = str(
                payload.get("messageId")
                or payload.get("openMessageId")
                or result.get("messageId")
                or result.get("openMessageId")
                or ""
            )
        except (ValueError, IndexError):
            payload = {"raw": raw_text}
        return SendResult(platform_msg_id=platform_id, raw=payload)

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
            raise CapabilityError("钉钉单文件不能超过 20MB")
        if text:
            await self.send_text(account, to_platform_id, text)
        token = await self._legacy_access_token()
        async with httpx.AsyncClient(timeout=120) as client:
            upload = await client.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": token, "type": "file"},
                files={"media": (file.filename, file.data, file.mime_type or "application/octet-stream")},
            )
            upload_data = self._json(upload)
            media_id = upload_data.get("media_id")
            if upload.status_code >= 400 or not media_id:
                raise PlatformError(
                    f"钉钉文件上传失败: {upload_data.get('errmsg') or upload.status_code}",
                    code="dingtalk_file_upload",
                )
            headers = await self._headers()
            if to_platform_id.startswith("cid") or to_platform_id.startswith("$"):
                resp = await client.post(
                    f"{BASE}/v1.0/robot/groupMessages/send",
                    headers=headers,
                    json={
                        "robotCode": self._robot_code,
                        "openConversationId": to_platform_id,
                        "msgKey": "sampleFile",
                        "msgParam": json.dumps(
                            {
                                "mediaId": media_id,
                                "fileName": file.filename,
                                "fileType": file.mime_type,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
            else:
                resp = await client.post(
                    f"{BASE}/v1.0/robot/oToMessages/batchSend",
                    headers=headers,
                    json={
                        "robotCode": self._robot_code,
                        "userIds": [to_platform_id],
                        "msgKey": "sampleFile",
                        "msgParam": json.dumps(
                            {
                                "mediaId": media_id,
                                "fileName": file.filename,
                                "fileType": file.mime_type,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
        data = self._check(resp)
        return SendResult(platform_msg_id=data.get("processQueryKey", ""), raw=data)

    async def create_task(
        self, account: AccountContext, draft: TaskDraft, assignee: AccountContext | None = None
    ) -> PlatformTaskRef:
        if not account.union_id:
            raise PlatformError("钉钉待办需要 unionId，请先完成钉钉登录或补充人员映射", code="dingtalk_union_missing")
        headers = await self._headers()
        executor = assignee.union_id if assignee and assignee.union_id else account.union_id
        body: dict[str, Any] = {
            "subject": draft.title[:1024],
            "description": (draft.description or "")[:4096],
            "executorIds": [executor],
            "sourceId": draft.unified_id,
            "bizTag": "relay",
        }
        if draft.due_at:
            body["dueTime"] = int(draft.due_at.timestamp() * 1000)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{BASE}/v1.0/todo/users/{account.union_id}/tasks",
                headers=headers,
                json=body,
            )
        data = self._check(resp)
        task_id = str(data.get("id") or data.get("taskId") or "")
        if not task_id:
            raise PlatformError(f"钉钉待办创建未返回 ID: {data}", code="dingtalk_todo_no_id")
        return PlatformTaskRef(platform_task_id=task_id, platform_status="open", meta={"raw": data})

    async def update_task_status(self, account: AccountContext, link: Any, status: str, token: str) -> None:
        if not account.union_id:
            raise PlatformError("钉钉待办需要 unionId", code="dingtalk_union_missing")
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.put(
                f"{BASE}/v1.0/todo/users/{account.union_id}/tasks/{link.platform_task_id}",
                params={"operatorId": account.union_id},
                headers=headers,
                json={"done": status == "done"},
            )
        self._check(resp)

    async def fetch_task(self, account: AccountContext, link: Any) -> PlatformTaskState:
        if not account.union_id:
            raise PlatformError("钉钉待办需要 unionId", code="dingtalk_union_missing")
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{BASE}/v1.0/todo/users/{account.union_id}/tasks/{link.platform_task_id}",
                headers=headers,
            )
        data = self._check(resp)
        done = bool(data.get("done"))
        stage = data.get("statusStage")
        status = "done" if done else ("in_progress" if stage in (1, "1") else "open")
        return PlatformTaskState(
            status=status,
            raw_status=str(stage if stage is not None else done),
            updated_at=None,
            title=data.get("subject"),
        )

    async def download_file(self, account: AccountContext, file: Any) -> bytes:
        download_code = file.platform_file_key if hasattr(file, "platform_file_key") else file.get("platform_file_key")
        if not download_code:
            raise PlatformError("钉钉文件缺少 downloadCode", code="dingtalk_file_ref")
        data, _ = await self.download_media(account, download_code)
        return data

    async def list_directory_users(self, account: AccountContext) -> list[dict[str, Any]]:
        """List the full DingTalk directory (requires department scopes)."""
        token = await self._legacy_access_token()
        users: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=30) as client:
            async def list_departments(dept_id: int) -> list[int]:
                resp = await client.post(
                    "https://oapi.dingtalk.com/topapi/v2/department/listsub",
                    params={"access_token": token},
                    json={"dept_id": dept_id, "language": "zh_CN"},
                )
                data = self._json(resp)
                if data.get("errcode", 0) != 0:
                    raise PlatformError(
                        f"钉钉部门查询失败: {data.get('errmsg') or data.get('sub_msg')}",
                        code="dingtalk_department_scope",
                    )
                return [
                    int(item.get("dept_id"))
                    for item in data.get("result") or []
                    if item.get("dept_id") is not None
                ]

            async def collect(dept_id: int) -> None:
                cursor = 0
                while True:
                    resp = await client.post(
                        "https://oapi.dingtalk.com/topapi/v2/user/list",
                        params={"access_token": token},
                        json={
                            "dept_id": dept_id,
                            "cursor": cursor,
                            "size": 100,
                            "language": "zh_CN",
                        },
                    )
                    data = self._json(resp)
                    if data.get("errcode", 0) != 0:
                        raise PlatformError(
                            f"钉钉成员查询失败: {data.get('errmsg') or data.get('sub_msg')}",
                            code="dingtalk_member_scope",
                        )
                    result = data.get("result") or {}
                    for item in result.get("list") or []:
                        user_id = str(item.get("userid") or item.get("user_id") or "")
                        if user_id:
                            users[user_id] = {
                                "open_id": user_id,
                                "name": item.get("name") or "钉钉联系人",
                                "union_id": item.get("unionid"),
                                "email": item.get("email") or item.get("org_email"),
                                "title": item.get("title") or "",
                            }
                    if not result.get("has_more"):
                        break
                    cursor = int(result.get("next_cursor") or 0)

            root_id = 1
            await collect(root_id)
            for child in await list_departments(root_id):
                await collect(child)
                for grandchild in await list_departments(child):
                    await collect(grandchild)

            # Enrich email/title/position. This field permission is separate in
            # some tenants; keep the basic list usable when it is not granted.
            for user_id, item in list(users.items())[:200]:
                try:
                    resp = await client.post(
                        "https://oapi.dingtalk.com/topapi/v2/user/get",
                        params={"access_token": token},
                        json={"userid": user_id, "language": "zh_CN"},
                    )
                    data = self._json(resp)
                    detail = data.get("result") or {}
                    if data.get("errcode", 0) != 0 or not isinstance(detail, dict):
                        continue
                    email = detail.get("email") or detail.get("org_email")
                    if email:
                        item["email"] = email
                    title = detail.get("title") or detail.get("position")
                    if title:
                        item["title"] = title
                except Exception:  # noqa: BLE001
                    continue
        return list(users.values())

    async def download_media(
        self, account: AccountContext, download_code: str
    ) -> tuple[bytes, str]:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{BASE}/v1.0/robot/messageFiles/download",
                headers=headers,
                json={"downloadCode": download_code, "robotCode": self._robot_code},
            )
            data = self._check(resp)
            url = data.get("downloadUrl")
            if not url:
                raise PlatformError("钉钉文件下载未返回 downloadUrl", code="dingtalk_file_url")
            file_resp = await client.get(url, timeout=60)
        if file_resp.status_code >= 400:
            raise PlatformError(f"钉钉文件下载 HTTP {file_resp.status_code}", code="dingtalk_file_download")
        mime = (file_resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
        content = file_resp.content
        if mime in {"application/octet-stream", "binary/octet-stream"}:
            if content.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif content.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif content.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
                mime = "image/webp"
        return content, mime
