from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_seed_and_core_pages(auth_client: httpx.AsyncClient):
    me = await auth_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["display_name"] == "林知远"

    messages = (await auth_client.get("/api/messages")).json()["items"]
    assert len(messages) == 7
    assert {message["platform"] for message in messages} == {"feishu", "dingtalk", "wecom"}

    tasks = (await auth_client.get("/api/tasks")).json()
    assert tasks["counts"] == {"all": 4, "open": 3, "in_progress": 1, "done": 0, "active": 4}

    files = (await auth_client.get("/api/files")).json()["items"]
    assert len(files) == 4

    contacts = (await auth_client.get("/api/contacts")).json()["items"]
    assert len(contacts) == 5


@pytest.mark.asyncio
async def test_reply_and_complete_closed_loop(auth_client: httpx.AsyncClient, app):
    tasks = (await auth_client.get("/api/tasks")).json()["items"]
    task = next(item for item in tasks if item["title"] == "确认 Q4 预算表")
    response = await auth_client.post(
        f"/api/tasks/{task['id']}/action",
        json={
            "client_action_id": "test-action-1",
            "reply_text": "预算已确认，市场费用没有问题。",
            "target_status": "done",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"]["status"] == "succeeded"
    assert body["task"]["status"] == "done"
    assert body["task"]["sync_state"] == "synced"
    assert body["action"]["steps"]["reply"]["status"] == "ok"

    feishu = app.state.adapters.get("feishu")
    assert feishu.calls["reply_text"] == 1
    assert feishu.calls["update_task_status"] == 1
    assert feishu.outbox[-1]["kind"] in {"reply", "send"}

    # A retry of the exact same client action must not send or write twice.
    retry = await auth_client.post(
        f"/api/tasks/{task['id']}/action",
        json={"client_action_id": "test-action-1", "target_status": "done"},
    )
    assert retry.status_code == 200
    assert feishu.calls["reply_text"] == 1
    assert feishu.calls["update_task_status"] == 1


@pytest.mark.asyncio
async def test_platform_echo_is_suppressed(auth_client: httpx.AsyncClient, app):
    tasks = (await auth_client.get("/api/tasks")).json()["items"]
    task = next(item for item in tasks if item["title"] == "确认 Q4 预算表")
    await auth_client.post(
        f"/api/tasks/{task['id']}/action",
        json={"client_action_id": "echo-action-1", "target_status": "done"},
    )
    feishu = app.state.adapters.get("feishu")
    before = feishu.calls["update_task_status"]

    event = await auth_client.post(
        "/api/dev/task-events",
        json={
            "platform": "feishu",
            "platform_task_id": "ft_budget_001",
            "status": "done",
            "event_id": "echo-event-1",
        },
    )
    assert event.status_code == 200
    assert event.json()["status"] in {"echo", "unchanged"}
    assert feishu.calls["update_task_status"] == before

    # Replaying the same event is idempotent.
    duplicate = await auth_client.post(
        "/api/dev/task-events",
        json={
            "platform": "feishu",
            "platform_task_id": "ft_budget_001",
            "status": "done",
            "event_id": "echo-event-1",
        },
    )
    assert duplicate.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_cross_platform_propagation_and_echo(auth_client: httpx.AsyncClient, app):
    tasks = (await auth_client.get("/api/tasks")).json()["items"]
    task = next(item for item in tasks if item["title"] == "发布 v2.3 版本")
    feishu = app.state.adapters.get("feishu")
    dingtalk = app.state.adapters.get("dingtalk")

    # Feishu side completes -> DingTalk must be updated.
    response = await auth_client.post(
        "/api/dev/task-events",
        json={"platform": "feishu", "platform_task_id": "ft_release_002", "status": "done"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "propagated"
    assert dingtalk.calls["update_task_status"] == 1

    detail = (await auth_client.get(f"/api/tasks/{task['id']}")).json()
    assert detail["status"] == "done"
    assert {link["platform_status"] for link in detail["links"]} == {"done"}

    # The propagated DingTalk write echoes back; it must not write Feishu again.
    before = feishu.calls["update_task_status"]
    echo = await auth_client.post(
        "/api/dev/task-events",
        json={"platform": "dingtalk", "platform_task_id": "dt_release_002", "status": "done"},
    )
    assert echo.json()["status"] in {"echo", "unchanged"}
    assert feishu.calls["update_task_status"] == before


@pytest.mark.asyncio
async def test_message_to_task_and_source_context(auth_client: httpx.AsyncClient):
    messages = (await auth_client.get("/api/messages")).json()["items"]
    message = next(item for item in messages if "路线图" in item["text"])
    response = await auth_client.post(
        "/api/tasks",
        json={
            "source_message_id": message["id"],
            "title": "评审新版路线图",
            "sync_platform": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["task"]["sync_state"] == "synced"
    assert body["task"]["source_message_id"] == message["id"]
    assert len(body["task"]["links"]) == 1

    context = await auth_client.get(f"/api/messages/{message['id']}/context?before=2&after=2")
    assert context.status_code == 200
    assert context.json()["target_id"] == message["id"]
    assert len(context.json()["items"]) >= 3


@pytest.mark.asyncio
async def test_file_open_download_and_view(auth_client: httpx.AsyncClient):
    files = (await auth_client.get("/api/files")).json()["items"]
    # SVG is intentionally not previewable (script-bearing document), so the
    # preview assertion uses the seeded PDF instead.
    file = next(item for item in files if item["filename"].endswith(".pdf"))
    opened = await auth_client.post(f"/api/files/{file['id']}/open")
    assert opened.status_code == 200
    body = opened.json()
    assert body["previewable"] is True
    assert body["file"]["open_count"] >= 1

    content = await auth_client.get(f"/api/files/{file['id']}/content")
    assert content.status_code == 200
    assert "application/pdf" in content.headers["content-type"]

    download = await auth_client.get(f"/api/files/{file['id']}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]

    recent = (await auth_client.get("/api/files?recent=true")).json()["items"]
    assert any(item["id"] == file["id"] for item in recent)


@pytest.mark.asyncio
async def test_send_idempotency(auth_client: httpx.AsyncClient, app):
    contacts = (await auth_client.get("/api/contacts?platform=feishu")).json()["items"]
    target = next(item for item in contacts if item["display_name"] == "周予安")
    payload = {
        "platform": "feishu",
        "to_user_id": target["id"],
        "text": "测试消息",
        "client_msg_id": "send-once-1",
    }
    first = await auth_client.post("/api/send", json=payload)
    assert first.status_code == 200, first.text
    second = await auth_client.post("/api/send", json=payload)
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert app.state.adapters.get("feishu").calls["send_text"] == 1


@pytest.mark.asyncio
async def test_send_file(auth_client: httpx.AsyncClient, app):
    contacts = (await auth_client.get("/api/contacts?platform=feishu")).json()["items"]
    target = next(item for item in contacts if item["display_name"] == "周予安")
    response = await auth_client.post(
        "/api/send/file",
        data={
            "platform": "feishu",
            "to_user_id": target["id"],
            "text": "附件说明",
            "client_msg_id": "file-send-once-1",
        },
        files={"file": ("relay-test.txt", b"hello from relay", "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_type"] == "file"
    assert body["delivery_status"] == "sent"
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "relay-test.txt"
    assert app.state.adapters.get("feishu").calls["send_file"] == 1
    assert app.state.adapters.get("feishu").outbox[-1]["kind"] == "send_file"

    found = await auth_client.get("/api/files", params={"q": "relay-test"})
    assert found.status_code == 200
    assert any(item["filename"] == "relay-test.txt" for item in found.json()["items"])

    # The file is also visible in the outbound message stream.
    messages = (await auth_client.get("/api/messages")).json()["items"]
    outbound = next(item for item in messages if item["id"] == body["id"])
    assert outbound["files"][0]["filename"] == "relay-test.txt"
