from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from backend.app.models import Message, User
from backend.app.services.ingest import InboundMessageData, ingest_message


async def _create_rule(client: httpx.AsyncClient, **overrides) -> dict:
    payload = {
        "name": "报价咨询自动回复",
        "platform": None,
        "match_type": "keyword",
        "keywords": "报价,价格",
        "reply_text": "您好，报价稍后由销售同事回复您。",
        "enabled": True,
        "priority": 100,
        "cooldown_seconds": 0,
        **overrides,
    }
    response = await client.post("/api/auto-reply/rules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _simulate(client: httpx.AsyncClient, text: str, chat_id: str = "sim_auto", platform: str = "feishu"):
    response = await client.post(
        "/api/dev/messages",
        json={
            "platform": platform,
            "text": text,
            "chat_id": chat_id,
            "chat_type": "direct",
            "sender_handle": "ou_bob",
            "sender_name": "周予安",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _auto_replies(client: httpx.AsyncClient, chat_id: str) -> list[dict]:
    conversations = (await client.get("/api/conversations")).json()["items"]
    match = next((item for item in conversations if item["platform_chat_id"] == chat_id), None)
    if match is None:
        return []
    items = (await client.get(f"/api/conversations/{match['id']}/messages")).json()["items"]
    return [item for item in items if item.get("auto_reply_rule_id")]


@pytest.mark.asyncio
async def test_rule_crud(auth_client: httpx.AsyncClient):
    listed = await auth_client.get("/api/auto-reply/rules")
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert listed.json()["enabled"] is True

    rule = await _create_rule(auth_client)
    assert rule["match_type"] == "keyword"
    assert rule["keyword_list"] == ["报价", "价格"]
    assert rule["hit_count"] == 0

    updated = await auth_client.put(
        f"/api/auto-reply/rules/{rule['id']}",
        json={
            "name": "改过的规则",
            "platform": "dingtalk",
            "match_type": "any",
            "keywords": "",
            "reply_text": "收到",
            "enabled": False,
            "priority": 10,
            "cooldown_seconds": 5,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["platform"] == "dingtalk"
    assert updated.json()["enabled"] is False

    deleted = await auth_client.delete(f"/api/auto-reply/rules/{rule['id']}")
    assert deleted.status_code == 200
    assert (await auth_client.get("/api/auto-reply/rules")).json()["items"] == []
    assert (await auth_client.delete(f"/api/auto-reply/rules/{rule['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_keyword_rule_requires_keywords(auth_client: httpx.AsyncClient):
    response = await auth_client.post(
        "/api/auto-reply/rules",
        json={"name": "空关键词", "match_type": "keyword", "keywords": "", "reply_text": "hi"},
    )
    assert response.status_code == 400
    assert "关键词" in response.json()["detail"]


@pytest.mark.asyncio
async def test_inbound_message_gets_auto_reply(auth_client: httpx.AsyncClient, app):
    rule = await _create_rule(auth_client)
    await _simulate(auth_client, "麻烦发一份报价给我")

    replies = await _auto_replies(auth_client, "sim_auto")
    assert len(replies) == 1
    reply = replies[0]
    assert reply["text"] == rule["reply_text"]
    assert reply["auto_reply_rule_id"] == rule["id"]
    assert reply["direction"] == "outbound"

    feishu = app.state.adapters.get("feishu")
    assert feishu.outbox[-1]["kind"] == "reply"

    refreshed = (await auth_client.get("/api/auto-reply/rules")).json()["items"][0]
    assert refreshed["hit_count"] == 1
    assert refreshed["last_hit_at"]

    log = (await auth_client.get("/api/auto-reply/log")).json()["items"]
    assert log and log[0]["rule_name"] == rule["name"]


@pytest.mark.asyncio
async def test_non_matching_text_gets_no_reply(auth_client: httpx.AsyncClient):
    await _create_rule(auth_client)
    await _simulate(auth_client, "今天下午的评审几点开始？", chat_id="sim_nomatch")
    assert await _auto_replies(auth_client, "sim_nomatch") == []


@pytest.mark.asyncio
async def test_disabled_rule_gets_no_reply(auth_client: httpx.AsyncClient):
    await _create_rule(auth_client, enabled=False)
    await _simulate(auth_client, "报价多少？", chat_id="sim_disabled")
    assert await _auto_replies(auth_client, "sim_disabled") == []


@pytest.mark.asyncio
async def test_platform_scoped_rule_is_ignored_elsewhere(auth_client: httpx.AsyncClient):
    await _create_rule(auth_client, platform="dingtalk")
    await _simulate(auth_client, "报价多少？", chat_id="sim_platform", platform="feishu")
    assert await _auto_replies(auth_client, "sim_platform") == []


@pytest.mark.asyncio
async def test_any_rule_answers_when_no_keyword_matches(auth_client: httpx.AsyncClient):
    await _create_rule(auth_client, name="兜底", match_type="any", keywords="", priority=900)
    await _simulate(auth_client, "你们几点下班", chat_id="sim_any")
    replies = await _auto_replies(auth_client, "sim_any")
    assert len(replies) == 1


@pytest.mark.asyncio
async def test_cooldown_suppresses_second_reply(auth_client: httpx.AsyncClient):
    await _create_rule(auth_client, cooldown_seconds=3600)
    await _simulate(auth_client, "报价一", chat_id="sim_cooldown")
    await _simulate(auth_client, "报价二", chat_id="sim_cooldown")
    replies = await _auto_replies(auth_client, "sim_cooldown")
    assert len(replies) == 1


@pytest.mark.asyncio
async def test_auto_reply_ignores_its_own_echo(auth_client: httpx.AsyncClient, app):
    rule = await _create_rule(auth_client)
    async with app.state.db.session() as session:
        owner = await session.scalar(select(User).where(User.email == "demo@relay.local"))
        assert owner is not None
        session.add(
            Message(
                owner_id=owner.id,
                platform="feishu",
                direction="outbound",
                platform_chat_id="oc_echo",
                chat_type="direct",
                text="在吗",
                sender_name="我",
                delivery_status="sent",
                client_msg_id="echo-out-1",
            )
        )
        await session.commit()
        await ingest_message(
            session,
            settings=app.state.settings,
            bus=app.state.bus,
            owner_id=owner.id,
            adapters=app.state.adapters,
            data=InboundMessageData(
                platform="feishu",
                platform_msg_id="echo-in-1",
                text="在吗",
                chat_id="oc_echo",
                sender_platform_id="ou_bob",
                sender_name="周予安",
            ),
        )
        replies = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.auto_reply_rule_id.is_not(None),
                        Message.platform_chat_id == "oc_echo",
                    )
                )
            ).all()
        )
    assert replies == []
    assert rule["id"]


@pytest.mark.asyncio
async def test_auto_reply_disabled_by_setting(
    auth_client: httpx.AsyncClient, app, monkeypatch: pytest.MonkeyPatch
):
    await _create_rule(auth_client)
    monkeypatch.setattr(app.state.settings, "auto_reply_enabled", False)
    await _simulate(auth_client, "报价多少？", chat_id="sim_setting")
    assert await _auto_replies(auth_client, "sim_setting") == []


def test_render_template_substitutes_known_variables():
    from types import SimpleNamespace

    from backend.app.services.auto_reply import render_template

    rule = SimpleNamespace(name="报价规则")
    message = SimpleNamespace(sender_name="张三", platform="feishu")
    rendered = render_template(
        "你好 {{sender_name}}，来自 {{platform}} 的 {{rule_name}}", rule=rule, message=message
    )
    assert rendered == "你好 张三，来自 飞书 的 报价规则"
    assert "{{" not in rendered


@pytest.mark.asyncio
async def test_attachment_asset_upload_and_reply(auth_client: httpx.AsyncClient, app):
    upload = await auth_client.post(
        "/api/auto-reply/assets",
        files={"file": ("报价单.csv", "sku,price\nA,10\n".encode(), "text/csv")},
    )
    assert upload.status_code == 201, upload.text
    asset = upload.json()
    assert asset["filename"] == "报价单.csv"

    rule = await _create_rule(
        auth_client,
        keywords="价格",
        reply_text="您好 {{sender_name}}，报价单见附件。",
        attachment_file_id=asset["id"],
    )
    assert rule["attachment"]["id"] == asset["id"]
    assert rule["attachment"]["filename"] == "报价单.csv"

    await _simulate(auth_client, "价格发我一下", chat_id="sim_attach")
    replies = await _auto_replies(auth_client, "sim_attach")
    assert len(replies) == 1
    reply = replies[0]
    assert reply["content_type"] == "file"
    assert reply["text"] == "您好 周予安，报价单见附件。"
    assert reply["files"] and reply["files"][0]["filename"] == "报价单.csv"

    feishu = app.state.adapters.get("feishu")
    assert feishu.outbox[-1]["kind"] == "send_file"
    assert feishu.outbox[-1]["filename"] == "报价单.csv"
    assert feishu.outbox[-1]["text"] == "您好 周予安，报价单见附件。"

    # Rule assets must not pollute the conversation file drawer.
    files = (await auth_client.get("/api/files")).json()["items"]
    assert all(item["id"] != asset["id"] for item in files)


@pytest.mark.asyncio
async def test_unknown_attachment_is_rejected(auth_client: httpx.AsyncClient):
    response = await auth_client.post(
        "/api/auto-reply/rules",
        json={
            "name": "坏附件",
            "match_type": "any",
            "keywords": "",
            "reply_text": "hi",
            "attachment_file_id": "does-not-exist",
        },
    )
    assert response.status_code == 400
    assert "附件" in response.json()["detail"]
