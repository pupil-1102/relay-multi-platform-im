from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from backend.app.models import PlatformConfig


def _platform(items: list[dict], platform: str) -> dict:
    return next(item for item in items if item["platform"] == platform)


def _field(payload: dict, key: str) -> dict:
    return next(field for field in payload["fields"] if field["key"] == key)


@pytest.mark.asyncio
async def test_platform_config_list_shape(auth_client: httpx.AsyncClient):
    response = await auth_client.get("/api/platforms/config")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["platform"] for item in items] == ["feishu", "dingtalk", "wecom"]
    feishu = _platform(items, "feishu")
    assert feishu["source"] == "environment"
    assert feishu["enabled"] is False
    secret = _field(feishu, "feishu_app_secret")
    assert secret["kind"] == "secret"
    assert secret["value"] == ""
    assert secret["secret_set"] is False


@pytest.mark.asyncio
async def test_save_platform_config_applies_and_masks_secret(
    auth_client: httpx.AsyncClient, app
):
    response = await auth_client.put(
        "/api/platforms/feishu/config",
        json={
            "enabled": True,
            "values": {
                "feishu_app_id": "cli_test_123",
                "feishu_app_secret": "super-secret-value",
                "feishu_redirect_uri": "http://localhost:8000/api/auth/feishu/callback",
            },
        },
    )
    assert response.status_code == 200, response.text
    assert "super-secret-value" not in response.text

    feishu = _platform(response.json()["items"], "feishu")
    assert feishu["enabled"] is True
    assert feishu["source"] == "database"
    assert feishu["configured"] is True
    secret = _field(feishu, "feishu_app_secret")
    assert secret["value"] == ""
    assert secret["secret_set"] is True

    # The running app sees the new values without a restart.
    assert app.state.settings.feishu_app_id == "cli_test_123"
    assert app.state.settings.feishu_enabled is True
    assert app.state.settings.feishu_app_secret == "super-secret-value"

    # Secrets are encrypted at rest.
    async with app.state.db.session() as session:
        row = await session.get(PlatformConfig, "feishu")
        assert row is not None and row.config_enc
        assert "super-secret-value" not in row.config_enc


@pytest.mark.asyncio
async def test_blank_secret_keeps_the_stored_one(auth_client: httpx.AsyncClient, app):
    first = await auth_client.put(
        "/api/platforms/feishu/config",
        json={"enabled": False, "values": {"feishu_app_id": "cli_a", "feishu_app_secret": "first-secret"}},
    )
    assert first.status_code == 200, first.text

    second = await auth_client.put(
        "/api/platforms/feishu/config",
        json={"enabled": False, "values": {"feishu_app_id": "cli_b", "feishu_app_secret": ""}},
    )
    assert second.status_code == 200, second.text
    assert app.state.settings.feishu_app_id == "cli_b"
    assert app.state.settings.feishu_app_secret == "first-secret"


@pytest.mark.asyncio
async def test_enabling_without_credentials_is_rejected(auth_client: httpx.AsyncClient):
    response = await auth_client.put(
        "/api/platforms/dingtalk/config", json={"enabled": True, "values": {}}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Client ID" in detail


@pytest.mark.asyncio
async def test_wecom_aibot_only_requires_bot_credentials(auth_client: httpx.AsyncClient):
    response = await auth_client.put(
        "/api/platforms/wecom/config",
        json={
            "enabled": True,
            "values": {"wecom_aibot_only": True},
        },
    )
    assert response.status_code == 400
    assert "Bot ID" in response.json()["detail"]

    ok = await auth_client.put(
        "/api/platforms/wecom/config",
        json={
            "enabled": True,
            "values": {
                "wecom_aibot_only": True,
                "wecom_aibot_id": "aib_test",
                "wecom_aibot_secret": "aibot-secret",
            },
        },
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_reset_restores_environment_values(auth_client: httpx.AsyncClient, app):
    saved = await auth_client.put(
        "/api/platforms/feishu/config",
        json={"enabled": True, "values": {"feishu_app_id": "cli_x", "feishu_app_secret": "s"}},
    )
    assert saved.status_code == 200, saved.text
    assert app.state.settings.feishu_app_id == "cli_x"

    reset = await auth_client.delete("/api/platforms/feishu/config")
    assert reset.status_code == 200, reset.text
    feishu = _platform(reset.json()["items"], "feishu")
    assert feishu["source"] == "environment"
    assert feishu["enabled"] is False
    assert app.state.settings.feishu_app_id == ""
    assert app.state.settings.feishu_enabled is False

    async with app.state.db.session() as session:
        row = await session.get(PlatformConfig, "feishu")
        assert row is None


@pytest.mark.asyncio
async def test_unknown_platform_is_rejected(auth_client: httpx.AsyncClient):
    response = await auth_client.put(
        "/api/platforms/slack/config", json={"enabled": True, "values": {}}
    )
    assert response.status_code == 400
