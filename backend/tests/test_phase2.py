from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from backend.app.models import PlatformAccount, User
from backend.app.security import decrypt_secret, encrypt_secret
from backend.app.services.accounts import context_for_user
from backend.app.services.realtime import DwsSidecar
from backend.app.services.runners import FeishuRunner


async def _user_and_account(app, platform: str):
    async with app.state.db.session() as session:
        user = await session.scalar(select(User).where(User.email == "demo@relay.local"))
        assert user is not None
        account = await session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.user_id == user.id,
                PlatformAccount.platform == platform,
            )
        )
        assert account is not None
        return user, account


@pytest.mark.asyncio
async def test_feishu_user_token_refresh(auth_client: httpx.AsyncClient, app):
    _, account = await _user_and_account(app, "feishu")
    settings = app.state.settings
    async with app.state.db.session() as session:
        account = await session.get(PlatformAccount, account.id)
        account.access_token_enc = encrypt_secret("old-token", settings)
        account.refresh_token_enc = encrypt_secret("refresh-token", settings)
        account.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
        user_id = account.user_id

    with respx.mock(assert_all_called=True) as mock:
        refresh = mock.post("https://accounts.feishu.cn/oauth/v3/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": "new-token",
                    "refresh_token": "new-refresh",
                    "expires_in": 7200,
                },
            )
        )
        async with app.state.db.session() as session:
            ctx = await context_for_user(session, user_id, "feishu", settings)
            account = await session.get(PlatformAccount, account.id)
            assert ctx.access_token == "new-token"
            assert decrypt_secret(account.access_token_enc, settings) == "new-token"
            assert decrypt_secret(account.refresh_token_enc, settings) == "new-refresh"
            assert account.token_expires_at > datetime.now(UTC)
            assert refresh.called
            body = refresh.calls[0].request.content.decode()
            assert "grant_type=refresh_token" in body
            assert "client_id=cli_test" in body or "client_id=" in body


@pytest.mark.asyncio
async def test_dingtalk_user_token_refresh(app):
    _, account = await _user_and_account(app, "dingtalk")
    settings = app.state.settings
    async with app.state.db.session() as session:
        account = await session.get(PlatformAccount, account.id)
        account.access_token_enc = encrypt_secret("old-token", settings)
        account.refresh_token_enc = encrypt_secret("refresh-token", settings)
        account.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
        user_id = account.user_id

    with respx.mock(assert_all_called=True) as mock:
        refresh = mock.post("https://api.dingtalk.com/v1.0/oauth2/userAccessToken").mock(
            return_value=httpx.Response(
                200,
                json={"accessToken": "dt-new", "refreshToken": "dt-refresh", "expireIn": 7200},
            )
        )
        async with app.state.db.session() as session:
            ctx = await context_for_user(session, user_id, "dingtalk", settings)
            assert ctx.access_token == "dt-new"
            assert refresh.called


@pytest.mark.asyncio
async def test_feishu_v2_task_event_ingested(auth_client: httpx.AsyncClient, app):
    user, _ = await _user_and_account(app, "feishu")
    runner = FeishuRunner(app.state.settings, app.state.db, app.state.adapters, app.state.bus, user.id)
    await runner._ingest_task_event(
        "ft_budget_001", "feishu-v2-event-1", event_type="task.v2.user_access[task_completed_update]"
    )
    async with app.state.db.session() as session:
        from backend.app.models import ProcessedEvent, Task

        event = await session.scalar(
            select(ProcessedEvent).where(ProcessedEvent.event_id == "feishu-v2-event-1")
        )
        assert event is not None
        task = await session.scalar(select(Task).where(Task.title == "确认 Q4 预算表"))
        assert task is not None


@pytest.mark.asyncio
async def test_wecom_jsapi_config_demo(auth_client: httpx.AsyncClient):
    response = await auth_client.get("/api/wecom/jsapi-config", params={"url": "https://relay.test/tasks"})
    assert response.status_code == 200
    assert response.json()["demo"] is True


@pytest.mark.asyncio
async def test_dws_event_parser(app, monkeypatch):
    user, _ = await _user_and_account(app, "dingtalk")
    sidecar = DwsSidecar(app.state.settings, app.state.db, app.state.adapters, app.state.bus)
    sidecar._owner_id = user.id
    captured = []

    async def fake_ingest(session, **kwargs):
        captured.append(kwargs["data"])

    monkeypatch.setattr("backend.app.services.realtime.ingest_task_event", fake_ingest)
    await sidecar._handle_line(
        json.dumps(
            {
                "type": "user_todo_task_update",
                "event_id": "dws-test-1",
                "task_id": "dt_contract_001",
                "status_stage": 2,
                "timestamp": 1760000000000,
                "subject": "客户续约",
            }
        )
    )
    assert captured
    assert captured[0].status == "done"
    assert captured[0].platform_task_id == "dt_contract_001"
    assert captured[0].event_id == "dws-test-1"


@pytest.mark.asyncio
async def test_dws_reconnect_status_exposes_next_retry(app):
    from datetime import UTC, datetime, timedelta

    sidecar = DwsSidecar(app.state.settings, app.state.db, app.state.adapters, app.state.bus)
    sidecar.status = {"state": "running", "detail": "", "events": 3}
    sidecar._last_error = "the subscription feature is not available"
    sidecar._mark_reconnecting(1, 1851)
    status = sidecar.status
    assert status["state"] == "reconnecting"
    assert status["events"] == 3
    assert status["last_exit_code"] == 1
    assert status["last_error"] == "the subscription feature is not available"
    retry_at = datetime.fromisoformat(status["next_retry_at"])
    assert retry_at > datetime.now(UTC) + timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_file_search_by_sender_and_message(auth_client: httpx.AsyncClient):
    by_sender = await auth_client.get("/api/files", params={"q": "周予安"})
    assert by_sender.status_code == 200
    items = by_sender.json()["items"]
    assert items
    assert all(item["sender_name"] == "周予安" for item in items)
    assert any(item["message_text"] for item in items)

    by_message = await auth_client.get("/api/files", params={"q": "路线图"})
    assert by_message.status_code == 200
    assert any(item["filename"].endswith(".svg") for item in by_message.json()["items"])
