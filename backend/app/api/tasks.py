from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from ..deps import AdaptersDep, BusDep, CurrentUser, SessionDep, SettingsDep
from ..models import LinkState, Message, SyncState, Task, TaskLink
from ..schemas import CreateTaskRequest, TaskActionRequest, WeComLinkRequest
from ..services.payloads import task_payload
from ..services.tasks import (
    ActionInput,
    build_task_payload,
    create_task_from_message,
    get_task,
    list_task_payloads,
    perform_task_action,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    session: SessionDep,
    user: CurrentUser,
    status: str | None = None,
    platform: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    items = await list_task_payloads(session, user.id, status=status, platform=platform, q=q)
    rows = (
        await session.execute(
            select(Task.status, func.count()).where(Task.owner_id == user.id).group_by(Task.status)
        )
    ).all()
    counts = {"all": 0, "open": 0, "in_progress": 0, "done": 0}
    for task_status, count in rows:
        counts[task_status] = count
        counts["all"] += count
    counts["active"] = counts["open"] + counts["in_progress"]
    return {"items": items, "counts": counts}


@router.post("")
async def create_task(
    body: CreateTaskRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict[str, Any]:
    message = await session.get(Message, body.source_message_id)
    if message is None or message.owner_id != user.id:
        raise HTTPException(status_code=404, detail="源消息不存在")
    if body.assignee_id:
        from ..models import User

        assignee = await session.get(User, body.assignee_id)
        if assignee is None:
            raise HTTPException(status_code=400, detail="负责人不存在")
    task, created = await create_task_from_message(
        session,
        settings=settings,
        adapters=adapters,
        bus=bus,
        user=user,
        message=message,
        title=body.title,
        description=body.description,
        assignee_id=body.assignee_id,
        due_at=body.due_at,
        sync_platform=body.sync_platform,
    )
    return {"task": await build_task_payload(session, task), "created": created}


@router.get("/{task_id}")
async def task_detail(task_id: str, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    task = await get_task(session, task_id, user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    return await build_task_payload(session, task)


@router.post("/{task_id}/action")
async def task_action(
    task_id: str,
    body: TaskActionRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict[str, Any]:
    task = await get_task(session, task_id, user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    if not body.reply_text and not body.target_status:
        raise HTTPException(status_code=400, detail="至少需要回复内容或目标状态")
    if body.target_status and body.target_status not in {"open", "in_progress", "done"}:
        raise HTTPException(status_code=400, detail="无效的状态")
    try:
        action, fresh = await perform_task_action(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            user=user,
            task=task,
            payload=ActionInput(
                client_action_id=body.client_action_id,
                action_type=body.action_type,
                reply_text=body.reply_text,
                target_status=body.target_status,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        steps = json.loads(action.steps_json or "{}")
    except ValueError:
        steps = {}
    return {
        "action": {
            "id": action.id,
            "client_action_id": action.client_action_id,
            "status": action.status,
            "steps": steps,
        },
        "task": await build_task_payload(session, fresh),
    }


@router.post("/{task_id}/wecom-link")
async def link_wecom_todo(
    task_id: str,
    body: WeComLinkRequest,
    session: SessionDep,
    user: CurrentUser,
    bus: BusDep,
) -> dict[str, Any]:
    task = await get_task(session, task_id, user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    link = await session.scalar(
        select(TaskLink).where(
            TaskLink.platform == "wecom", TaskLink.platform_task_id == body.todo_id
        )
    )
    if link is not None and link.task_id != task.id:
        raise HTTPException(status_code=409, detail="该企微待办已关联其他任务")
    if link is None:
        link = TaskLink(
            task_id=task.id,
            platform="wecom",
            platform_task_id=body.todo_id,
            platform_status=task.status,
            link_state=LinkState.ACTIVE,
            last_synced_at=None,
        )
        session.add(link)
    if task.sync_state == SyncState.LOCAL:
        task.sync_state = SyncState.SYNCED
        task.sync_error = None
    await session.commit()
    fresh = await get_task(session, task.id, user.id)
    assert fresh is not None
    payload = await build_task_payload(session, fresh)
    await bus.publish("task.updated", payload)
    return payload
