from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import AdaptersDep, CurrentUser, SessionDep, SettingsDep
from ..platform.base import PlatformError
from ..services.contacts import (
    list_contacts,
    sync_dingtalk_contacts,
    sync_feishu_contacts,
    sync_wecom_contacts,
)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@router.get("")
async def get_contacts(
    session: SessionDep,
    user: CurrentUser,
    q: str | None = None,
    platform: str | None = None,
) -> dict:
    items = await list_contacts(session, q=q, platform=platform)
    return {"items": items}


@router.post("/sync")
async def sync_contacts(
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
    platform: str = "feishu",
) -> dict:
    if platform == "feishu":
        try:
            return await sync_feishu_contacts(
                session, settings=settings, adapters=adapters, owner_id=user.id
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
    if platform == "dingtalk":
        try:
            return await sync_dingtalk_contacts(
                session, settings=settings, owner_id=user.id, adapters=adapters
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
    if platform == "wecom":
        try:
            return await sync_wecom_contacts(
                session, settings=settings, owner_id=user.id, adapters=adapters
            )
        except PlatformError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
    raise HTTPException(status_code=400, detail="暂不支持该平台通讯录同步")
