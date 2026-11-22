from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import PlatformAccount
from ..platform.base import AccountContext
from ..security import decrypt_secret


async def get_account(session: AsyncSession, user_id: str, platform: str) -> PlatformAccount | None:
    return await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.user_id == user_id,
            PlatformAccount.platform == platform,
        )
    )


async def get_account_by_handle(session: AsyncSession, platform: str, handle: str) -> PlatformAccount | None:
    return await session.scalar(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.platform_user_id == handle,
        )
    )


def context_for(account: PlatformAccount, settings: Settings) -> AccountContext:
    return AccountContext(
        platform=account.platform,
        user_id=account.user_id,
        platform_user_id=account.platform_user_id,
        union_id=account.union_id,
        display_name=account.display_name,
        access_token=decrypt_secret(account.access_token_enc, settings),
        refresh_token=decrypt_secret(account.refresh_token_enc, settings),
    )


async def context_for_user(session: AsyncSession, user_id: str, platform: str, settings: Settings) -> AccountContext:
    account = await get_account(session, user_id, platform)
    if account is None:
        from ..platform.base import PlatformError

        raise PlatformError(f"当前用户没有绑定 {platform} 账号", code="account_missing")
    from .tokens import ensure_fresh_account

    return await ensure_fresh_account(session, account, settings)


async def bind_wecom_aibot_operator(
    session: AsyncSession,
    *,
    owner_id: str,
    sender_id: str,
    sender_name: str,
    settings: Settings,
) -> bool:
    """Bind the operator's WeCom identity from their first aibot message.

    Aibot mode has no OAuth round trip: the bot only learns a ``userid`` from
    the message body. In this single-operator deployment the first sender is the
    operator, so bind that identity once. Doing this explicitly (instead of
    during ingest) keeps the rule visible and easy to audit; a later OAuth login
    refreshes the same account.
    """
    if not settings.single_operator or not sender_id:
        return False
    if await get_account(session, owner_id, "wecom") is not None:
        return False
    session.add(
        PlatformAccount(
            user_id=owner_id,
            platform="wecom",
            platform_user_id=sender_id,
            display_name=sender_name or sender_id,
            status="connected",
        )
    )
    await session.commit()
    return True
