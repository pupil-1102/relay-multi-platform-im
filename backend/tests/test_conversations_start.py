from __future__ import annotations

import httpx
import pytest

from backend.app.platform.base import PlatformError
from backend.app.services import contacts as contacts_service


async def _contact(client: httpx.AsyncClient, platform: str, name: str | None = None) -> dict:
    items = (await client.get("/api/contacts")).json()["items"]
    for item in items:
        handles = [entry for entry in item["platforms"] if entry["platform"] == platform]
        if not handles:
            continue
        if name is None or item["display_name"] == name:
            return item
    raise AssertionError(f"no contact bound to {platform} (name={name})")


def _handle(contact: dict, platform: str) -> str:
    return next(entry["handle"] for entry in contact["platforms"] if entry["platform"] == platform)


@pytest.mark.asyncio
async def test_wecom_start_creates_visible_conversation(auth_client: httpx.AsyncClient, app):
    """WeCom only returns a msgid; the peer userid must become the chat key."""
    contact = await _contact(auth_client, "wecom")
    handle = _handle(contact, "wecom")

    response = await auth_client.post(
        "/api/conversations/start",
        json={
            "platform": "wecom",
            "contact_id": contact["id"],
            "text": "你好，发起企业微信私聊",
            "client_msg_id": "start-wecom-1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"]["delivery_status"] == "sent"
    assert body["message"]["platform_chat_id"] == handle

    conversations = (await auth_client.get("/api/conversations")).json()["items"]
    assert body["conversation_id"] in {item["id"] for item in conversations}

    wecom = app.state.adapters.get("wecom")
    assert wecom.outbox[-1]["kind"] == "send"
    assert wecom.outbox[-1]["to"] == handle


@pytest.mark.asyncio
async def test_feishu_start_with_external_open_id(auth_client: httpx.AsyncClient):
    response = await auth_client.post(
        "/api/conversations/start",
        json={
            "platform": "feishu",
            "handle": "ou_external_person_0001",
            "display_name": "外部联系人",
            "text": "你好，这是外部联系人私聊",
            "client_msg_id": "start-feishu-1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"]["platform_chat_id"] == "ou_external_person_0001"

    conversations = (await auth_client.get("/api/conversations")).json()["items"]
    started = next(item for item in conversations if item["id"] == body["conversation_id"])
    assert started["platform"] == "feishu"
    assert started["title"] == "外部联系人"

    # The external contact is persisted only after a successful delivery.
    contacts = (await auth_client.get("/api/contacts")).json()["items"]
    assert any(item["display_name"] == "外部联系人" for item in contacts)


@pytest.mark.asyncio
async def test_dingtalk_start_without_cid_fails_before_sending(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def no_chat(settings, name):  # noqa: ANN001
        return ""

    monkeypatch.setattr(contacts_service, "resolve_dingtalk_conversation", no_chat)
    contact = await _contact(auth_client, "dingtalk", "周予安")
    before = len((await auth_client.get("/api/messages")).json()["items"])

    response = await auth_client.post(
        "/api/conversations/start",
        json={
            "platform": "dingtalk",
            "contact_id": contact["id"],
            "text": "钉钉发起会话",
            "client_msg_id": "start-dingtalk-nocid",
        },
    )
    assert response.status_code == 502, response.text
    assert "cid" in response.json()["detail"]

    after = (await auth_client.get("/api/messages")).json()["items"]
    assert len(after) == before, "a failed start must not leave a delivered orphan message"


@pytest.mark.asyncio
async def test_dingtalk_start_surfaces_dws_errors(
    auth_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def boom(settings, name):  # noqa: ANN001
        raise PlatformError("DWS 通讯录查询失败: dws 未登录", code="dingtalk_dws_contacts")

    monkeypatch.setattr(contacts_service, "resolve_dingtalk_conversation", boom)
    contact = await _contact(auth_client, "dingtalk", "周予安")

    response = await auth_client.post(
        "/api/conversations/start",
        json={
            "platform": "dingtalk",
            "contact_id": contact["id"],
            "text": "钉钉发起会话",
            "client_msg_id": "start-dingtalk-dwserror",
        },
    )
    assert response.status_code == 502, response.text
    assert "dws" in response.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_start_with_resolved_cid(
    auth_client: httpx.AsyncClient, app, monkeypatch: pytest.MonkeyPatch
):
    async def cid(settings, name):  # noqa: ANN001
        assert name == "周予安"
        return "cid_test_conversation_001"

    monkeypatch.setattr(contacts_service, "resolve_dingtalk_conversation", cid)
    contact = await _contact(auth_client, "dingtalk", "周予安")

    response = await auth_client.post(
        "/api/conversations/start",
        json={
            "platform": "dingtalk",
            "contact_id": contact["id"],
            "text": "钉钉发起会话",
            "client_msg_id": "start-dingtalk-cid",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"]["platform_chat_id"] == "cid_test_conversation_001"

    dingtalk = app.state.adapters.get("dingtalk")
    assert dingtalk.outbox[-1]["to"] == "cid_test_conversation_001"
