from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from backend.app.models import PlatformAccount, User
from backend.app.services.auth import AuthError, _same_person, _upsert_identity


async def _register(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "bind-check-2026", "display_name": "绑定测试"},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]["id"]


async def _demo_user(app) -> User:
    async with app.state.db.session() as session:
        user = await session.scalar(select(User).where(User.email == "demo@relay.local"))
    assert user is not None
    return user


def test_same_person_requires_a_matching_email():
    assert _same_person(User(email="a@example.com"), User(email="A@Example.com")) is True
    assert _same_person(User(email=""), User(email="")) is False
    assert _same_person(User(email=None), User(email="a@example.com")) is False
    assert _same_person(User(email="a@example.com"), User(email="b@example.com")) is False


@pytest.mark.asyncio
async def test_oauth_identity_bound_to_another_user_is_rejected(
    client: httpx.AsyncClient, app
):
    """Logging in with someone else's platform identity must not swap workspaces."""
    new_user_id = await _register(client, "binding@example.com")
    demo = await _demo_user(app)

    async with app.state.db.session() as session:
        with pytest.raises(AuthError) as excinfo:
            await _upsert_identity(
                session,
                settings=app.state.settings,
                platform="feishu",
                handle="ou_demo_lin",
                union_id=None,
                display_name="林知远",
                email=None,
                access_token=None,
                refresh_token=None,
                expires_in=None,
                link_user_id=new_user_id,
            )
    assert "已绑定" in str(excinfo.value)

    async with app.state.db.session() as session:
        assert await session.get(User, new_user_id) is not None, "the registered account must survive"
        assert await session.get(User, demo.id) is not None
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "feishu",
                PlatformAccount.platform_user_id == "ou_demo_lin",
            )
        )
        assert account is not None and account.user_id == demo.id


@pytest.mark.asyncio
async def test_new_identity_binds_to_the_current_account(client: httpx.AsyncClient, app):
    new_user_id = await _register(client, "bind-new@example.com")

    async with app.state.db.session() as session:
        user = await _upsert_identity(
            session,
            settings=app.state.settings,
            platform="feishu",
            handle="ou_brand_new_identity_001",
            union_id="on_brand_new",
            display_name="绑定测试",
            email=None,
            access_token="token-value",
            refresh_token="refresh-value",
            expires_in=3600,
            link_user_id=new_user_id,
        )
    assert user.id == new_user_id

    async with app.state.db.session() as session:
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "feishu",
                PlatformAccount.platform_user_id == "ou_brand_new_identity_001",
            )
        )
    assert account is not None
    assert account.user_id == new_user_id
    assert account.status == "connected"


@pytest.mark.asyncio
async def test_unbind_identity_keeps_messages_and_frees_the_handle(
    auth_client: httpx.AsyncClient, app
):
    me = (await auth_client.get("/api/auth/me")).json()
    owner_id = me["user"]["id"]
    assert {account["platform"] for account in me["accounts"]} == {"feishu", "dingtalk", "wecom"}
    messages_before = len((await auth_client.get("/api/messages")).json()["items"])
    assert messages_before > 0

    response = await auth_client.delete("/api/auth/accounts/feishu")
    assert response.status_code == 200, response.text
    assert {account["platform"] for account in response.json()["accounts"]} == {"dingtalk", "wecom"}

    async with app.state.db.session() as session:
        row = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "feishu",
                PlatformAccount.user_id == owner_id,
            )
        )
    assert row is None, "the identity row (and its tokens) must be removed"

    messages_after = len((await auth_client.get("/api/messages")).json()["items"])
    assert messages_after == messages_before, "unbinding must not delete history"

    # A second delete has nothing left to remove.
    assert (await auth_client.delete("/api/auth/accounts/feishu")).status_code == 404

    # The handle is free again: it can be bound to the same account later.
    async with app.state.db.session() as session:
        rebound = await _upsert_identity(
            session,
            settings=app.state.settings,
            platform="feishu",
            handle="ou_demo_lin",
            union_id="on_demo_lin",
            display_name="林知远",
            email=None,
            access_token="new-token",
            refresh_token=None,
            expires_in=3600,
            link_user_id=owner_id,
        )
    assert rebound.id == owner_id

    me_again = (await auth_client.get("/api/auth/me")).json()
    assert "feishu" in {account["platform"] for account in me_again["accounts"]}


@pytest.mark.asyncio
async def test_unbind_only_touches_the_current_account(client: httpx.AsyncClient, app):
    new_user_id = await _register(client, "no-accounts@example.com")
    assert new_user_id

    # The fresh account owns no platform identity; another account's identity
    # must never be removable from here.
    response = await client.delete("/api/auth/accounts/feishu")
    assert response.status_code == 404
    assert "没有绑定" in response.json()["detail"]

    async with app.state.db.session() as session:
        demo_account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == "feishu",
                PlatformAccount.platform_user_id == "ou_demo_lin",
            )
        )
    assert demo_account is not None, "the other account's identity must be untouched"


@pytest.mark.asyncio
async def test_unbind_unknown_platform_is_rejected(auth_client: httpx.AsyncClient):
    response = await auth_client.delete("/api/auth/accounts/slack")
    assert response.status_code == 404
