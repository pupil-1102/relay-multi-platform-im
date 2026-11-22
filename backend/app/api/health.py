from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ..deps import CurrentUser, SessionDep, SettingsDep
from ..models import Message, Task
from .conversations import unread_total

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(session: SessionDep, settings: SettingsDep) -> dict:
    await session.execute(select(1))
    return {
        "status": "ok",
        "app": settings.app_name,
        "demo_mode": settings.demo_mode,
        "real_channels": settings.enable_real_channels,
        "feishu_user_mirror_enabled": settings.feishu_user_mirror_enabled,
        "dingtalk_dws_im_mirror_enabled": settings.dingtalk_dws_im_mirror_enabled,
        "version": "1.0.0",
    }


@router.get("/realtime/status")
async def realtime_status(request: Request, user: CurrentUser) -> dict:
    return request.app.state.realtime.status()


@router.get("/platforms/status")
async def platforms_status(request: Request, user: CurrentUser) -> dict:
    return {
        "platforms": request.app.state.runners.status(),
        "realtime": request.app.state.realtime.status(),
        "bot": request.app.state.runners.info(),
        "settings": {
            "demo_mode": request.app.state.settings.demo_mode,
            "multi_user_channels": request.app.state.settings.multi_user_channels,
        },
    }


@router.get("/overview")
async def overview(session: SessionDep, user: CurrentUser) -> dict:
    message_count = await session.scalar(
        select(func.count()).select_from(Message).where(Message.owner_id == user.id)
    )
    task_count = await session.scalar(
        select(func.count()).select_from(Task).where(Task.owner_id == user.id)
    )
    open_count = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.owner_id == user.id, Task.status.in_(["open", "in_progress"]))
    )
    return {
        "messages": message_count or 0,
        "tasks": task_count or 0,
        "active_tasks": open_count or 0,
        "unread": await unread_total(session, user.id),
    }
