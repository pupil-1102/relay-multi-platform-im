from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import FileRecord, FileView, Message, PlatformAccount, Task, TaskLink, User
from .emoji import normalize_feishu_emoji


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "avatar_url": user.avatar_url,
    }


def account_payload(account: PlatformAccount) -> dict[str, Any]:
    return {
        "platform": account.platform,
        "display_name": account.display_name,
        "status": account.status,
        "platform_user_id": account.platform_user_id,
    }


def file_payload(file: FileRecord, view: FileView | None = None, message: Message | None = None) -> dict[str, Any]:
    return {
        "id": file.id,
        "filename": file.filename,
        "mime_type": file.mime_type,
        "size_bytes": file.size_bytes,
        "platform": file.platform,
        "message_id": file.message_id,
        "download_status": file.download_status,
        "created_at": iso(file.created_at),
        "opened": bool(view and view.open_count),
        "last_opened_at": iso(view.last_opened_at) if view else None,
        "open_count": view.open_count if view else 0,
        "sender_name": message.sender_name if message else None,
        "message_text": (message.text[:140] if message and message.text else None),
    }


def message_payload(
    message: Message,
    *,
    files: list[dict[str, Any]] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": message.id,
        "platform": message.platform,
        "direction": message.direction,
        "text": normalize_feishu_emoji(message.text) if message.platform == "feishu" else message.text,
        "content_type": message.content_type,
        "sender_name": message.sender_name,
        "sender_user_id": message.sender_user_id,
        "platform_chat_id": message.platform_chat_id,
        "chat_type": message.chat_type,
        "task_id": message.task_id,
        "auto_reply_rule_id": message.auto_reply_rule_id,
        "reply_to_message_id": message.reply_to_message_id,
        "reply_to_platform_msg_id": message.reply_to_platform_msg_id,
        "delivery_status": message.delivery_status,
        "error": message.error,
        "created_at": iso(message.created_at),
        "files": files or [],
        "task": task,
    }


def task_brief(task: Task | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "sync_state": task.sync_state,
        "source_platform": task.source_platform,
    }


def message_brief(message: Message | None) -> dict[str, Any] | None:
    if message is None:
        return None
    text = message.text.strip()
    if len(text) > 180:
        text = text[:180] + "…"
    return {
        "id": message.id,
        "platform": message.platform,
        "text": text,
        "sender_name": message.sender_name,
        "chat_type": message.chat_type,
        "created_at": iso(message.created_at),
    }


def link_payload(link: TaskLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "platform": link.platform,
        "platform_task_id": link.platform_task_id,
        "platform_status": link.platform_status,
        "last_synced_at": iso(link.last_synced_at),
        "state": link.link_state,
        "sync_pending": bool(link.sync_token and link.expected_until),
    }


def task_payload(
    task: Task,
    *,
    source_message: Message | None = None,
    assignee_name: str | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "source_platform": task.source_platform,
        "source_message_id": task.source_message_id,
        "origin": task.origin,
        "assignee_id": task.assignee_id,
        "assignee_name": assignee_name,
        "due_at": iso(task.due_at),
        "priority": task.priority,
        "sync_state": task.sync_state,
        "sync_error": task.sync_error,
        "version": task.version,
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
        "closed_at": iso(task.closed_at),
        "links": links if links is not None else [link_payload(link) for link in task.links],
        "source_message": message_brief(source_message),
    }


def contact_payload(user: User, accounts: list[PlatformAccount]) -> dict[str, Any]:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "initials": (user.display_name or "?")[:2],
        "platforms": [
            {
                "platform": account.platform,
                "handle": account.platform_user_id,
                "display_name": account.display_name,
            }
            for account in accounts
        ],
    }
