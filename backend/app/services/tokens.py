from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import PlatformAccount
from ..platform.base import AccountContext
from ..security import decrypt_secret, encrypt_secret
from .accounts import context_for

logger = logging.getLogger(__name__)


async def ensure_fresh_account(
    session: AsyncSession, account: PlatformAccount, settings: Settings
) -> AccountContext:
    """Return an AccountContext, refreshing a user token when it is near expiry."""
    ctx = context_for(account, settings)
    if account.token_expires_at is None or account.token_expires_at > datetime.now(UTC) + timedelta(seconds=90):
        return ctx
    refresh_token = decrypt_secret(account.refresh_token_enc, settings)
    if not refresh_token:
        return ctx
    try:
        if account.platform == "feishu":
            data = await _refresh_feishu(settings, refresh_token)
            access_token = data.get("access_token")
            new_refresh = data.get("refresh_token") or refresh_token
            expires_in = int(data.get("expires_in") or 7200)
        elif account.platform == "dingtalk":
            data = await _refresh_dingtalk(settings, refresh_token)
            access_token = data.get("accessToken")
            new_refresh = data.get("refreshToken") or refresh_token
            expires_in = int(data.get("expireIn") or 7200)
        else:
            return ctx
        if not access_token:
            return ctx
        account.access_token_enc = encrypt_secret(access_token, settings)
        account.refresh_token_enc = encrypt_secret(new_refresh, settings)
        account.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        account.status = "connected"
        await session.commit()
        logger.info("Refreshed %s user token for account %s", account.platform, account.id)
        return context_for(account, settings)
    except Exception:  # noqa: BLE001
        logger.warning("Token refresh failed for %s; keeping existing token", account.platform, exc_info=True)
        return ctx


async def _refresh_feishu(settings: Settings, refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://accounts.feishu.cn/oauth/v3/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": settings.feishu_app_id,
                "client_secret": settings.feishu_app_secret,
                "refresh_token": refresh_token,
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"Feishu refresh failed: {data.get('error') or data.get('code')} "
                f"{data.get('error_description') or ''}"
            )
        return data


async def _refresh_dingtalk(settings: Settings, refresh_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": settings.dingtalk_client_id,
                "clientSecret": settings.dingtalk_client_secret,
                "grantType": "refresh_token",
                "refreshToken": refresh_token,
            },
        )
        data = resp.json()
        if "accessToken" not in data:
            raise RuntimeError(f"DingTalk refresh failed: {data}")
        return data
