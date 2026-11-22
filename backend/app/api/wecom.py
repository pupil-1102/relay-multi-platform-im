from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import AdaptersDep, CurrentUser, SettingsDep

router = APIRouter(prefix="/api/wecom", tags=["wecom"])


@router.get("/jsapi-config")
async def jsapi_config(
    url: str,
    user: CurrentUser,
    settings: SettingsDep,
    adapters: AdaptersDep,
) -> dict:
    if settings.demo_mode:
        return {
            "demo": True,
            "corp_id": "demo-corp",
            "agent_id": "0",
            "timestamp": 0,
            "nonce_str": "demo",
            "signature": "demo",
            "url": url,
        }
    adapter = adapters.get("wecom")
    if not hasattr(adapter, "jsapi_config"):
        raise HTTPException(status_code=400, detail="当前企微 adapter 不支持 JS-SDK 签名")
    try:
        return await adapter.jsapi_config(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
