from __future__ import annotations

import httpx
import pytest


async def _register(client: httpx.AsyncClient, email: str = "new.user@example.com", password: str = "secret12345"):
    return await client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "新用户"},
    )


@pytest.mark.asyncio
async def test_register_login_logout(client: httpx.AsyncClient):
    response = await _register(client, email="New.User@Example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "new.user@example.com"
    assert body["user"]["display_name"] == "新用户"
    assert body["has_password"] is True
    assert body["allow_registration"] is True

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "new.user@example.com"

    await client.post("/api/auth/logout")
    assert (await client.get("/api/auth/me")).status_code == 401

    wrong = await client.post(
        "/api/auth/login",
        json={"email": "new.user@example.com", "password": "wrong-password"},
    )
    assert wrong.status_code == 401

    ok = await client.post(
        "/api/auth/login",
        json={"email": "new.user@example.com", "password": "secret12345"},
    )
    assert ok.status_code == 200, ok.text
    assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_duplicate_email_is_rejected(client: httpx.AsyncClient):
    assert (await _register(client)).status_code == 201
    await client.post("/api/auth/logout")
    again = await _register(client)
    assert again.status_code == 400
    assert "已注册" in again.json()["detail"]


@pytest.mark.asyncio
async def test_register_validates_email_and_password(client: httpx.AsyncClient):
    bad_email = await client.post(
        "/api/auth/register", json={"email": "not-an-email", "password": "secret12345"}
    )
    assert bad_email.status_code == 400
    assert "邮箱" in bad_email.json()["detail"]

    short = await client.post(
        "/api/auth/register", json={"email": "short@example.com", "password": "short"}
    )
    assert short.status_code == 400
    assert "密码" in short.json()["detail"]


@pytest.mark.asyncio
async def test_registration_can_be_disabled(
    client: httpx.AsyncClient, app, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(app.state.settings, "allow_registration", False)
    response = await _register(client)
    assert response.status_code == 403
    assert "注册已关闭" in response.json()["detail"]


@pytest.mark.asyncio
async def test_change_password_flow(client: httpx.AsyncClient):
    assert (await _register(client)).status_code == 201

    rejected = await client.post(
        "/api/auth/password", json={"password": "brand-new-secret", "current_password": "wrong"}
    )
    assert rejected.status_code == 400
    assert "当前密码" in rejected.json()["detail"]

    changed = await client.post(
        "/api/auth/password",
        json={"password": "brand-new-secret", "current_password": "secret12345"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["has_password"] is True

    await client.post("/api/auth/logout")
    old = await client.post(
        "/api/auth/login",
        json={"email": "new.user@example.com", "password": "secret12345"},
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/auth/login",
        json={"email": "new.user@example.com", "password": "brand-new-secret"},
    )
    assert new.status_code == 200, new.text


@pytest.mark.asyncio
async def test_new_account_gets_an_isolated_workspace(client: httpx.AsyncClient):
    assert (await _register(client)).status_code == 201
    conversations = (await client.get("/api/conversations")).json()["items"]
    assert conversations == []
    tasks = (await client.get("/api/tasks")).json()["counts"]
    assert tasks["all"] == 0
    files = (await client.get("/api/files")).json()["items"]
    assert files == []
    # Contacts are the shared enterprise directory, not per-account data.
    contacts = (await client.get("/api/contacts")).json()["items"]
    assert contacts


@pytest.mark.asyncio
async def test_password_hash_is_not_plaintext(client: httpx.AsyncClient, app):
    from sqlalchemy import select

    from backend.app.models import User

    assert (await _register(client, password="secret12345")).status_code == 201
    async with app.state.db.session() as session:
        user = await session.scalar(select(User).where(User.email == "new.user@example.com"))
    assert user is not None
    assert user.password_hash is not None
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert "secret12345" not in user.password_hash
