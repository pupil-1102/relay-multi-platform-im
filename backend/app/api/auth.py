from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from ..deps import CurrentUser, SessionDep, SettingsDep
from ..models import PlatformAccount
from ..schemas import LoginRequest, PasswordRequest, RegisterRequest, SendRequest  # noqa: F401
from ..services.auth import (
    AuthError,
    authenticate_user,
    authorize_url,
    consume_oauth_state,
    create_oauth_state,
    dev_login,
    exchange_oauth_code,
    get_user_from_request,
    register_user,
    set_session_cookie,
    set_user_password,
    user_auth_payload,
)
from ..services.auth import clear_session_cookie
router = APIRouter(prefix="/api/auth", tags=["auth"])
OAUTH_NEXT_COOKIE = "relay_oauth_next"
logger = logging.getLogger(__name__)


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _error_redirect(next_url: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in next_url else "?"
    response = RedirectResponse(f"{next_url}{separator}auth_error={quote(message)}")
    response.delete_cookie(OAUTH_NEXT_COOKIE, path="/")
    return response


@router.get("/config")
async def auth_config(settings: SettingsDep) -> dict:
    return {
        "demo_mode": settings.demo_mode,
        "dev_login": settings.dev_login,
        "allow_registration": settings.allow_registration,
        "configured": {
            "feishu": bool(settings.feishu_app_id and settings.feishu_app_secret),
            "dingtalk": bool(settings.dingtalk_client_id and settings.dingtalk_client_secret),
            "wecom": bool(settings.wecom_corp_id and settings.wecom_secret),
        },
    }


async def _start_channels(request: Request, user_id: str, settings: Settings) -> None:
    """Bring real-time channels up for the owner that just signed in."""
    if not (settings.enable_real_channels and not settings.demo_mode):
        return
    try:
        await request.app.state.realtime.start(user_id)
        await request.app.state.runners.start(user_id)
    except Exception:  # noqa: BLE001
        logger.debug("channel start after login failed", exc_info=True)


@router.post("/register")
async def register(
    body: RegisterRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> JSONResponse:
    try:
        user = await register_user(
            session,
            settings,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=403 if not settings.allow_registration else 400,
            detail=str(exc),
        ) from exc
    response = JSONResponse(await user_auth_payload(session, user, settings), status_code=201)
    set_session_cookie(response, user.id, settings)
    await _start_channels(request, user.id, settings)
    return response


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> JSONResponse:
    try:
        user = await authenticate_user(session, email=body.email, password=body.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response = JSONResponse(await user_auth_payload(session, user, settings))
    set_session_cookie(response, user.id, settings)
    await _start_channels(request, user.id, settings)
    return response


@router.post("/password")
async def change_password(
    body: PasswordRequest,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> dict:
    try:
        updated = await set_user_password(
            session,
            user,
            password=body.password,
            current_password=body.current_password,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await user_auth_payload(session, updated, settings)


@router.get("/me")
async def me(request: Request, session: SessionDep, settings: SettingsDep):
    user = await get_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return await user_auth_payload(session, user, settings)


@router.post("/dev-login")
async def dev_login_endpoint(session: SessionDep, settings: SettingsDep) -> JSONResponse:
    try:
        user_id = await dev_login(session, settings)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from ..models import User

    user = await session.get(User, user_id)
    assert user is not None
    response = JSONResponse(await user_auth_payload(session, user, settings))
    set_session_cookie(response, user.id, settings)
    return response


@router.post("/logout")
async def logout(settings: SettingsDep) -> Response:
    response = JSONResponse({"ok": True})
    clear_session_cookie(response, settings)
    return response


@router.delete("/accounts/{platform}")
async def unbind_account(
    platform: str,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
) -> dict:
    """Remove one platform identity from the current account.

    The row (and its encrypted tokens) is deleted so the identity can be bound
    to another Relay account later. Messages/tasks already ingested stay under
    this account.
    """
    if platform not in {"feishu", "dingtalk", "wecom"}:
        raise HTTPException(status_code=404, detail="不支持的平台")
    account = await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.user_id == user.id,
            PlatformAccount.platform == platform,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="当前账号没有绑定该平台身份")
    await session.delete(account)
    await session.commit()
    logger.info("unbound %s identity for user %s", platform, user.id)
    # Stop the mirrors that would otherwise keep polling a removed identity.
    from ..services.channels import reload_channels

    try:
        await reload_channels(request.app)
    except Exception:  # noqa: BLE001 - unbinding must succeed regardless
        logger.warning("channel reload after unbind failed", exc_info=True)
    return await user_auth_payload(session, user, settings)


@router.get("/{platform}/start")
async def oauth_start(
    platform: str, request: Request, session: SessionDep, settings: SettingsDep
) -> RedirectResponse:
    if platform not in {"feishu", "dingtalk", "wecom"}:
        raise HTTPException(status_code=404, detail="不支持的平台")
    configured = {
        "feishu": bool(settings.feishu_app_id and settings.feishu_app_secret),
        "dingtalk": bool(settings.dingtalk_client_id and settings.dingtalk_client_secret),
        "wecom": bool(settings.wecom_corp_id and settings.wecom_secret),
    }[platform]
    if not configured:
        raise HTTPException(status_code=400, detail=f"{platform} OAuth 未配置，请先填写环境变量")
    redirect_uri = {
        "feishu": settings.feishu_redirect_uri,
        "dingtalk": settings.dingtalk_redirect_uri,
        "wecom": settings.wecom_redirect_uri,
    }[platform]
    state = await create_oauth_state(session, platform, redirect_uri)
    response = RedirectResponse(authorize_url(settings, platform, state, redirect_uri))
    response.set_cookie(
        OAUTH_NEXT_COOKIE,
        _safe_next(request.query_params.get("next")),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.get("/{platform}/callback")
async def oauth_callback(
    platform: str,
    code: str,
    state: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> RedirectResponse:
    next_url = _safe_next(request.cookies.get(OAUTH_NEXT_COOKIE))
    current_user = await get_user_from_request(request)
    try:
        await consume_oauth_state(session, state, platform)
        user = await exchange_oauth_code(
            session,
            settings,
            platform,
            code,
            link_user_id=current_user.id if current_user else None,
        )
    except AuthError as exc:
        logger.warning("OAuth callback rejected for %s: %s", platform, exc)
        return _error_redirect(next_url, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("OAuth callback crashed for %s", platform)
        return _error_redirect(next_url, f"登录失败: {exc}")
    response = RedirectResponse(next_url)
    response.delete_cookie(OAUTH_NEXT_COOKIE, path="/")
    set_session_cookie(response, user.id, settings)
    if settings.enable_real_channels and not settings.demo_mode:
        try:
            await request.app.state.realtime.start(user.id)
            await request.app.state.runners.start(user.id)
        except Exception:  # noqa: BLE001
            pass
    return response
