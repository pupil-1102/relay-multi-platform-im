from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..security import decode_session_token

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    token = websocket.cookies.get(settings.session_cookie)
    user_id = decode_session_token(token, settings) if token else None
    if not user_id:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    bus = websocket.app.state.bus
    queue = await bus.subscribe()
    await websocket.send_json(
        {
            "type": "hello",
            "data": {"user_id": user_id, "demo_mode": settings.demo_mode},
        }
    )

    async def sender() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    async def receiver() -> None:
        while True:
            raw = await websocket.receive_text()
            if raw.strip() in {"ping", '"ping"'}:
                await websocket.send_json({"type": "pong", "data": {}})

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(queue)
        for task in (sender_task, receiver_task):
            if not task.done():
                task.cancel()
