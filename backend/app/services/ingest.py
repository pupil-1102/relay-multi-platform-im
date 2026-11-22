from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import (
    Direction,
    FileRecord,
    FileStatus,
    Message,
    Platform,
    PlatformAccount,
    ProcessedEvent,
    Task,
    TaskLink,
    TaskOrigin,
    TaskStatus,
    User,
)
from ..platform.registry import AdapterRegistry
from .events import EventBus
from .files import save_bytes
from .payloads import file_payload, message_payload
from .sync import from_platform_status
from .tasks import build_task_payload, sync_task_from_platform


@dataclass(slots=True)
class AttachmentData:
    filename: str
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    platform_file_key: str | None = None
    source_url: str | None = None
    aeskey: str | None = None
    content: bytes | None = None


@dataclass(slots=True)
class InboundMessageData:
    platform: str
    platform_msg_id: str
    text: str
    chat_id: str | None = None
    chat_type: str = "direct"
    sender_platform_id: str | None = None
    sender_name: str = ""
    content_type: str = "text"
    direction: str = "inbound"
    platform_created_at: datetime | None = None
    attachments: list[AttachmentData] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskEventData:
    platform: str
    event_id: str
    platform_task_id: str
    status: str
    event_type: str = "task.updated"
    title: str = ""
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


async def ingest_message(
    session: AsyncSession,
    *,
    settings: Settings,
    bus: EventBus,
    owner_id: str,
    data: InboundMessageData,
    adapters: AdapterRegistry | None = None,
) -> Message:
    existing = await session.scalar(
        select(Message).where(
            Message.owner_id == owner_id,
            Message.platform == data.platform,
            Message.platform_msg_id == data.platform_msg_id,
        )
    )
    if existing:
        return existing

    sender_user_id = None
    if data.sender_platform_id:
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == data.platform,
                PlatformAccount.platform_user_id == data.sender_platform_id,
            )
        )
        sender_user_id = account.user_id if account else None
        if account and (not data.sender_name or data.sender_name in {"飞书用户", "用户"}):
            mapped = await session.get(User, account.user_id)
            if mapped and mapped.display_name:
                data.sender_name = mapped.display_name

    message = Message(
        owner_id=owner_id,
        platform=data.platform,
        direction=Direction(data.direction),
        platform_msg_id=data.platform_msg_id,
        platform_chat_id=data.chat_id,
        chat_type=data.chat_type,
        sender_platform_id=data.sender_platform_id,
        sender_name=data.sender_name,
        sender_user_id=sender_user_id,
        content_type=data.content_type,
        text=data.text,
        raw_json=json.dumps(data.raw, ensure_ascii=False, default=str) if data.raw else None,
        platform_created_at=data.platform_created_at,
        created_at=data.platform_created_at or utcnow(),
        delivery_status="received",
    )
    session.add(message)
    await session.flush()

    files_out: list[dict[str, Any]] = []
    for attachment in data.attachments:
        record = FileRecord(
            owner_id=owner_id,
            message_id=message.id,
            platform=data.platform,
            platform_file_key=attachment.platform_file_key,
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
            source_url=attachment.source_url,
            aeskey=attachment.aeskey,
            download_status=FileStatus.PENDING,
        )
        if attachment.content is not None:
            try:
                key, digest, size = save_bytes(settings, attachment.content, attachment.filename)
                record.storage_key = key
                record.sha256 = digest
                record.size_bytes = size
                record.download_status = FileStatus.READY
            except OSError:
                record.download_status = FileStatus.FAILED
        session.add(record)
        await session.flush()
        files_out.append(file_payload(record, message=message))

    await session.commit()
    payload = message_payload(message, files=files_out)
    await bus.publish("message.created", payload)
    if message.direction == Direction.INBOUND:
        from .auto_reply import maybe_auto_reply

        try:
            await maybe_auto_reply(
                session,
                settings=settings,
                adapters=adapters,
                bus=bus,
                owner_id=owner_id,
                message=message,
            )
        except Exception:  # noqa: BLE001 - auto reply must never break ingest
            pass
    return message


async def ingest_task_event(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    bus: EventBus,
    owner_id: str,
    data: TaskEventData,
) -> dict[str, Any]:
    event_id = data.event_id or hashlib.sha256(
        json.dumps(
            {
                "platform": data.platform,
                "task": data.platform_task_id,
                "status": data.status,
                "type": data.event_type,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    processed = await session.scalar(
        select(ProcessedEvent).where(
            ProcessedEvent.platform == data.platform, ProcessedEvent.event_id == event_id
        )
    )
    if processed:
        return {"status": "duplicate", "event_id": event_id}

    record = ProcessedEvent(
        platform=data.platform,
        event_id=event_id,
        event_type=data.event_type,
        payload_hash=hashlib.sha256(json.dumps(data.raw, default=str).encode()).hexdigest()[:32],
    )
    session.add(record)
    await session.flush()

    link = await session.scalar(
        select(TaskLink).where(
            TaskLink.platform == data.platform,
            TaskLink.platform_task_id == data.platform_task_id,
        )
    )
    if link is None:
        status = from_platform_status(data.platform, data.status)
        task = Task(
            owner_id=owner_id,
            title=data.title or f"{data.platform} 待办 {data.platform_task_id}",
            description=f"来自 {data.platform} 的任务事件",
            status=status,
            source_platform=data.platform,
            origin=TaskOrigin.PLATFORM,
            assignee_id=owner_id,
            sync_state="synced",
            closed_at=utcnow() if status == TaskStatus.DONE else None,
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskLink(
                task_id=task.id,
                platform=data.platform,
                platform_task_id=data.platform_task_id,
                platform_status=status,
                last_synced_at=utcnow(),
                last_event_at=data.occurred_at or utcnow(),
            )
        )
        record.task_id = task.id
        record.outcome = "task_created"
        await session.commit()
        await session.refresh(task, ["links"])
        payload = await build_task_payload(session, task)
        await bus.publish("task.created", payload)
        return {"status": "task_created", "task_id": task.id, "event_id": event_id}

    result = await sync_task_from_platform(
        session,
        settings=settings,
        adapters=adapters,
        bus=bus,
        link=link,
        new_status=from_platform_status(data.platform, data.status),
        event_time=data.occurred_at,
    )
    record.task_id = link.task_id
    record.outcome = str(result.get("status"))
    await session.commit()
    return {"event_id": event_id, **result}
