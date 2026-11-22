from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..deps import AdaptersDep, BusDep, CurrentUser, SessionDep, SettingsDep
from ..platform.base import PlatformError
from ..schemas import SendRequest
from ..services.send import send_file as send_file_message
from ..services.send import send_text

router = APIRouter(prefix="/api/send", tags=["send"])


@router.post("")
async def send_message(
    body: SendRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
) -> dict:
    try:
        payload = await send_text(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            owner=user,
            platform=body.platform,
            text=body.text,
            to_user_id=body.to_user_id,
            to_handle=body.to_handle,
            client_msg_id=body.client_msg_id,
            reply_to_message_id=body.reply_to_message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if payload.get("delivery_status") == "failed":
        raise HTTPException(status_code=502, detail=payload.get("error") or "发送失败")
    return payload


@router.post("/file")
async def send_file_endpoint(
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    bus: BusDep,
    platform: str = Form(...),
    file: UploadFile = File(...),
    text: str | None = Form(None),
    to_user_id: str | None = Form(None),
    to_handle: str | None = Form(None),
    client_msg_id: str | None = Form(None),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    filename = file.filename or "未命名文件"
    try:
        payload = await send_file_message(
            session,
            settings=settings,
            adapters=adapters,
            bus=bus,
            owner=user,
            platform=platform,
            filename=filename,
            mime_type=file.content_type or "application/octet-stream",
            data=data,
            text=text,
            to_user_id=to_user_id,
            to_handle=to_handle,
            client_msg_id=client_msg_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if payload.get("delivery_status") == "failed":
        raise HTTPException(status_code=502, detail=payload.get("error") or "文件发送失败")
    return payload
