from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class PlatformError(Exception):
    def __init__(self, message: str, *, code: str = "platform_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class CapabilityError(PlatformError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="capability_not_supported", retryable=False)


@dataclass(slots=True)
class AccountContext:
    platform: str
    user_id: str
    platform_user_id: str
    union_id: str | None = None
    display_name: str = ""
    access_token: str | None = None
    refresh_token: str | None = None


@dataclass(slots=True)
class MessageRef:
    id: str
    platform: str
    platform_msg_id: str
    chat_id: str | None
    chat_type: str
    sender_platform_id: str | None
    sender_name: str = ""
    # Original inbound payload. Adapters need it to tell which channel a
    # conversation actually lives in (for example WeCom aibot vs app message).
    source_raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskDraft:
    unified_id: str
    title: str
    description: str = ""
    due_at: datetime | None = None
    assignee_platform_id: str | None = None
    owner_platform_id: str | None = None


@dataclass(slots=True)
class OutboundFile:
    filename: str
    mime_type: str
    data: bytes


@dataclass(slots=True)
class SendResult:
    platform_msg_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlatformTaskRef:
    platform_task_id: str
    platform_status: str = "open"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlatformTaskState:
    status: str
    raw_status: str = ""
    updated_at: datetime | None = None
    title: str | None = None


class PlatformAdapter(ABC):
    platform: str
    supports_task_creation: bool = True
    supports_task_update: bool = True
    supports_quote_reply: bool = True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @abstractmethod
    async def send_text(self, account: AccountContext, to_platform_id: str, text: str) -> SendResult:
        raise NotImplementedError

    @abstractmethod
    async def reply_text(self, account: AccountContext, message: MessageRef, text: str) -> SendResult:
        raise NotImplementedError

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
        raise CapabilityError(f"{self.platform} 暂不支持发送文件")

    @abstractmethod
    async def create_task(
        self, account: AccountContext, draft: TaskDraft, assignee: AccountContext | None = None
    ) -> PlatformTaskRef:
        raise NotImplementedError

    @abstractmethod
    async def update_task_status(self, account: AccountContext, link: Any, status: str, token: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def fetch_task(self, account: AccountContext, link: Any) -> PlatformTaskState:
        raise NotImplementedError

    @abstractmethod
    async def download_file(self, account: AccountContext, file: Any) -> bytes:
        raise NotImplementedError
