from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import AdaptersDep, BusDep, CurrentUser, SessionDep, SettingsDep
from ..schemas import SimulateMessageRequest, SimulateTaskEventRequest
from ..services.ingest import (
    InboundMessageData,
    TaskEventData,
    ingest_message,
    ingest_task_event,
)
from ..services.payloads import message_payload

router = APIRouter(prefix="/api/dev", tags=["dev"])


def _require_demo(settings) -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="模拟接口仅在演示模式可用")


@router.post("/messages")
async def simulate_message(
    body: SimulateMessageRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict:
    _require_demo(settings)
    message = await ingest_message(
        session,
        settings=settings,
        bus=bus,
        owner_id=user.id,
        adapters=adapters,
        data=InboundMessageData(
            platform=body.platform,
            platform_msg_id=f"sim-{body.platform}-{__import__('uuid').uuid4().hex[:10]}",
            text=body.text,
            chat_id=body.chat_id,
            chat_type=body.chat_type,
            sender_platform_id=body.sender_handle,
            sender_name=body.sender_name,
            content_type="text",
            raw={"simulated": True},
        ),
    )
    return message_payload(message)


@router.post("/task-events")
async def simulate_task_event(
    body: SimulateTaskEventRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict:
    _require_demo(settings)
    return await ingest_task_event(
        session,
        settings=settings,
        adapters=adapters,
        bus=bus,
        owner_id=user.id,
        data=TaskEventData(
            platform=body.platform,
            event_id=body.event_id or f"sim-{body.platform}-{body.platform_task_id}-{body.status}",
            platform_task_id=body.platform_task_id,
            status=body.status,
            event_type="task.updated",
            title=body.title,
            raw={"simulated": True},
        ),
    )


@router.get("/outbox")
async def outbox(settings: SettingsDep, adapters: AdaptersDep) -> dict:
    _require_demo(settings)
    result = {}
    for platform in adapters.platforms:
        adapter = adapters.get(platform)
        result[platform] = {
            "calls": dict(adapter.calls) if hasattr(adapter, "calls") else {},
            "outbox": adapter.outbox if hasattr(adapter, "outbox") else [],
            "tasks": adapter.tasks if hasattr(adapter, "tasks") else {},
        }
    return result
