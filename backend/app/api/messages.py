from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import CurrentUser, SessionDep
from ..models import FileView, Message, Task
from ..services.payloads import file_payload, message_payload, task_brief

router = APIRouter(prefix="/api/messages", tags=["messages"])


def _parse_before(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="before 参数不是合法时间") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _enrich(session, owner_id: str, user_id: str, messages: list[Message]) -> list[dict[str, Any]]:
    file_ids = [file.id for message in messages for file in message.files]
    views = {}
    if file_ids:
        rows = (
            await session.scalars(
                select(FileView).where(
                    FileView.user_id == user_id, FileView.file_id.in_(file_ids)
                )
            )
        ).all()
        views = {row.file_id: row for row in rows}

    task_ids = [message.task_id for message in messages if message.task_id]
    tasks: dict[str, Task] = {}
    if task_ids:
        rows = (await session.scalars(select(Task).where(Task.id.in_(task_ids)))).all()
        tasks = {task.id: task for task in rows}

    result = []
    for message in messages:
        files = [file_payload(file, views.get(file.id), message) for file in message.files]
        result.append(
            message_payload(
                message,
                files=files,
                task=task_brief(tasks.get(message.task_id)) if message.task_id else None,
            )
        )
    return result


@router.get("")
async def list_messages(
    session: SessionDep,
    user: CurrentUser,
    platform: str | None = None,
    q: str | None = None,
    chat_id: str | None = None,
    before: str | None = None,
    limit: int = Query(default=60, ge=1, le=200),
) -> dict[str, Any]:
    stmt = select(Message).where(Message.owner_id == user.id).options(selectinload(Message.files))
    if platform:
        stmt = stmt.where(Message.platform == platform)
    if chat_id:
        stmt = stmt.where(Message.platform_chat_id == chat_id)
    if q:
        stmt = stmt.where(Message.text.ilike(f"%{q}%"))
    parsed_before = _parse_before(before)
    if parsed_before:
        stmt = stmt.where(Message.created_at < parsed_before)
    stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
    messages = list((await session.scalars(stmt)).all())
    messages.reverse()
    items = await _enrich(session, user.id, user.id, messages)
    next_before = items[0]["created_at"] if len(messages) == limit and messages else None
    return {"items": items, "next_before": next_before}


@router.get("/{message_id}/context")
async def message_context(
    message_id: str,
    session: SessionDep,
    user: CurrentUser,
    before: int = Query(default=20, ge=0, le=100),
    after: int = Query(default=20, ge=0, le=100),
) -> dict[str, Any]:
    target = await session.scalar(
        select(Message)
        .where(Message.id == message_id, Message.owner_id == user.id)
        .options(selectinload(Message.files))
    )
    if target is None:
        raise HTTPException(status_code=404, detail="消息不存在")

    older_stmt = (
        select(Message)
        .where(Message.owner_id == user.id, Message.created_at < target.created_at)
        .options(selectinload(Message.files))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(before)
    )
    newer_stmt = (
        select(Message)
        .where(Message.owner_id == user.id, Message.created_at > target.created_at)
        .options(selectinload(Message.files))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(after)
    )
    older = list((await session.scalars(older_stmt)).all())
    older.reverse()
    newer = list((await session.scalars(newer_stmt)).all())
    items = await _enrich(session, user.id, user.id, [*older, target, *newer])
    target_index = sum(1 for message in older)
    return {"items": items, "target_id": target.id, "target_index": target_index}


@router.get("/{message_id}")
async def get_message(message_id: str, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    message = await session.scalar(
        select(Message)
        .where(Message.id == message_id, Message.owner_id == user.id)
        .options(selectinload(Message.files))
    )
    if message is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    data = await _enrich(session, user.id, user.id, [message])
    return data[0]
