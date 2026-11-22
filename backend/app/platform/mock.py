from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from .base import (
    AccountContext,
    MessageRef,
    OutboundFile,
    PlatformAdapter,
    PlatformError,
    PlatformTaskRef,
    PlatformTaskState,
    SendResult,
    TaskDraft,
)

# A lightweight vector image used by the demo file drawer.
DEMO_SVG = ("""<svg xmlns="http://www.w3.org/2000/svg" width="720" height="420" viewBox="0 0 720 420">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#1d4ed8"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs>
<rect width="720" height="420" fill="#0f172a"/>
<rect x="40" y="40" width="640" height="340" rx="24" fill="url(#g)" opacity="0.95"/>
<text x="80" y="130" fill="#ffffff" font-family="Segoe UI, sans-serif" font-size="34" font-weight="700">Product Roadmap</text>
<text x="80" y="175" fill="#dbeafe" font-family="Segoe UI, sans-serif" font-size="18">Relay - unified inbox &amp; task loop - 2026</text>
<rect x="80" y="220" width="240" height="16" rx="8" fill="#ffffff" opacity="0.55"/>
<rect x="80" y="252" width="380" height="16" rx="8" fill="#ffffff" opacity="0.4"/>
<rect x="80" y="284" width="300" height="16" rx="8" fill="#ffffff" opacity="0.3"/>
<circle cx="600" cy="300" r="46" fill="#ffffff" opacity="0.2"/>
<circle cx="600" cy="300" r="28" fill="#ffffff" opacity="0.35"/>
</svg>""").encode("utf-8")


class MockAdapter(PlatformAdapter):
    """Deterministic in-memory platform used by the demo and the test suite."""

    def __init__(self, platform: str, *, latency_ms: int = 0) -> None:
        self.platform = platform
        self.latency_ms = latency_ms
        self.outbox: list[dict[str, Any]] = []
        self.calls: Counter[str] = Counter()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self.fail_next: dict[str, int] = {}
        self._counter = itertools.count(1)

    async def _tick(self, op: str) -> None:
        self.calls[op] += 1
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        remaining = self.fail_next.get(op, 0)
        if remaining:
            self.fail_next[op] = remaining - 1
            raise PlatformError(f"mock {self.platform} {op} failure", code="mock_failure", retryable=True)

    def seed_task(self, platform_task_id: str, status: str, title: str = "") -> None:
        self.tasks[platform_task_id] = {
            "status": status,
            "updated_at": datetime.now(UTC),
            "title": title,
        }

    def seed_file(self, key: str, data: bytes) -> None:
        self.files[key] = data

    def simulate_task_change(self, platform_task_id: str, status: str) -> None:
        self.tasks.setdefault(platform_task_id, {})["status"] = status
        self.tasks[platform_task_id]["updated_at"] = datetime.now(UTC)

    def _next_id(self, kind: str) -> str:
        return f"mock-{self.platform}-{kind}-{next(self._counter)}"

    async def send_text(self, account: AccountContext, to_platform_id: str, text: str) -> SendResult:
        await self._tick("send_text")
        msg_id = self._next_id("msg")
        self.outbox.append(
            {
                "kind": "send",
                "platform": self.platform,
                "to": to_platform_id,
                "text": text,
                "platform_msg_id": msg_id,
            }
        )
        return SendResult(platform_msg_id=msg_id, raw={"mock": True})

    async def reply_text(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        await self._tick("reply_text")
        msg_id = self._next_id("reply")
        self.outbox.append(
            {
                "kind": "reply",
                "platform": self.platform,
                "to_message": message.platform_msg_id,
                "chat_id": message.chat_id,
                "text": text,
                "platform_msg_id": msg_id,
            }
        )
        return SendResult(platform_msg_id=msg_id, raw={"mock": True, "quoted": True})

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
        await self._tick("send_file")
        msg_id = self._next_id("file")
        self.outbox.append(
            {
                "kind": "send_file",
                "platform": self.platform,
                "to": to_platform_id,
                "filename": file.filename,
                "size": len(file.data),
                "text": text,
                "as_user": as_user,
                "platform_msg_id": msg_id,
            }
        )
        return SendResult(platform_msg_id=msg_id, raw={"mock": True})

    async def create_task(
        self, account: AccountContext, draft: TaskDraft, assignee: AccountContext | None = None
    ) -> PlatformTaskRef:
        await self._tick("create_task")
        task_id = self._next_id("task")
        self.tasks[task_id] = {
            "status": "open",
            "updated_at": datetime.now(UTC),
            "title": draft.title,
            "unified_id": draft.unified_id,
        }
        return PlatformTaskRef(platform_task_id=task_id, platform_status="open", meta={"mock": True})

    async def update_task_status(self, account: AccountContext, link: Any, status: str, token: str) -> None:
        await self._tick("update_task_status")
        task_id = getattr(link, "platform_task_id", None) or link["platform_task_id"]
        self.tasks.setdefault(task_id, {})["status"] = status
        self.tasks[task_id]["updated_at"] = datetime.now(UTC)
        self.tasks[task_id]["last_token"] = token

    async def fetch_task(self, account: AccountContext, link: Any) -> PlatformTaskState:
        await self._tick("fetch_task")
        task_id = getattr(link, "platform_task_id", None) or link["platform_task_id"]
        data = self.tasks.get(task_id, {"status": "open", "updated_at": None})
        return PlatformTaskState(
            status=data.get("status", "open"),
            raw_status=data.get("status", "open"),
            updated_at=data.get("updated_at"),
            title=data.get("title"),
        )

    async def download_file(self, account: AccountContext, file: Any) -> bytes:
        await self._tick("download_file")
        key = getattr(file, "platform_file_key", None) or file.get("platform_file_key")
        if key and key in self.files:
            return self.files[key]
        name = getattr(file, "filename", None) or file.get("filename", "file.txt")
        return f"演示文件：{name}\n由 Relay mock 存储生成。\n".encode()

    def outbox_for(self, kind: str) -> list[dict[str, Any]]:
        return [item for item in self.outbox if item["kind"] == kind]


def new_mock_adapters(latency_ms: int = 0) -> dict[str, MockAdapter]:
    return {name: MockAdapter(name, latency_ms=latency_ms) for name in ("feishu", "dingtalk", "wecom")}


__all__ = ["MockAdapter", "DEMO_SVG", "new_mock_adapters"]
