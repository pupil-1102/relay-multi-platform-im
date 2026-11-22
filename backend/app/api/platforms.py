from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import CurrentUser, SessionDep, SettingsDep
from ..schemas import PlatformConfigRequest
from ..services.channels import reload_channels
from ..services.platform_config import (
    PlatformConfigError,
    platform_config_payload,
    reset_platform_config,
    save_platform_config,
)

router = APIRouter(prefix="/api/platforms", tags=["platforms"])


@router.get("/config")
async def list_platform_configs(
    session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> dict:
    return {"items": await platform_config_payload(session, settings)}


@router.put("/{platform}/config")
async def update_platform_config(
    platform: str,
    body: PlatformConfigRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> dict:
    try:
        saved = await save_platform_config(
            session,
            settings,
            platform,
            enabled=body.enabled,
            values=body.values,
            updated_by=user.id,
        )
    except PlatformConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await reload_channels(request.app)
    return {
        "ok": True,
        "saved": saved,
        "items": await platform_config_payload(session, settings),
        "platforms": request.app.state.runners.status(),
    }


@router.delete("/{platform}/config")
async def delete_platform_config(
    platform: str,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> dict:
    try:
        await reset_platform_config(
            session, settings, request.app.state.env_settings, platform
        )
    except PlatformConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await reload_channels(request.app)
    return {
        "ok": True,
        "items": await platform_config_payload(session, settings),
        "platforms": request.app.state.runners.status(),
    }
