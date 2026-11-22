from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..deps import AdaptersDep, CurrentUser, SessionDep, SettingsDep
from ..models import Message
from ..platform.base import PlatformError
from ..services.files import (
    content_disposition,
    ensure_ready,
    file_payloads,
    get_file,
    is_previewable,
    record_open,
)
from ..services.payloads import file_payload

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("")
async def list_files(
    session: SessionDep,
    user: CurrentUser,
    q: str | None = None,
    platform: str | None = None,
    recent: bool = False,
) -> dict:
    items = await file_payloads(
        session,
        owner_id=user.id,
        user_id=user.id,
        q=q,
        platform=platform,
        recent=recent,
    )
    return {"items": items}


@router.get("/{file_id}")
async def file_detail(file_id: str, session: SessionDep, user: CurrentUser) -> dict:
    file = await get_file(session, file_id, user.id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    message = await session.get(Message, file.message_id) if file.message_id else None
    return file_payload(file, message=message)


@router.post("/{file_id}/open")
async def open_file(
    file_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
) -> dict:
    file = await get_file(session, file_id, user.id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        await ensure_ready(session, settings=settings, adapters=adapters, file=file)
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"文件准备失败: {exc}") from exc
    view = await record_open(session, file, user.id)
    return {
        "file": file_payload(file, view),
        "previewable": is_previewable(file.mime_type),
        "content_url": f"/api/files/{file.id}/content",
        "download_url": f"/api/files/{file.id}/download",
    }


@router.get("/{file_id}/content")
async def file_content(
    file_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
):
    file = await get_file(session, file_id, user.id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        path = await ensure_ready(session, settings=settings, adapters=adapters, file=file)
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    inline = is_previewable(file.mime_type)
    headers = {
        "Content-Disposition": content_disposition(file.filename, inline=inline),
        "Cache-Control": "private, max-age=60",
        "X-Content-Type-Options": "nosniff",
        # Anything opened directly from here is untrusted content: sandbox it so
        # a document cannot script the app origin even if its type slips through.
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'",
    }
    return FileResponse(
        path,
        media_type=file.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
):
    file = await get_file(session, file_id, user.id)
    if file is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        path = await ensure_ready(session, settings=settings, adapters=adapters, file=file)
    except PlatformError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    await record_open(session, file, user.id)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers=headers,
        filename=None,
    )
