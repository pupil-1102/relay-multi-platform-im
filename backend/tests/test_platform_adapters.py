from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from backend.app.config import Settings
from backend.app.platform.base import AccountContext, MessageRef, TaskDraft
from backend.app.platform.dingtalk import DingTalkAdapter
from backend.app.platform.feishu import FeishuAdapter
from backend.app.platform.wecom import WeComAdapter


def settings() -> Settings:
    return Settings(
        demo_mode=False,
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        dingtalk_client_id="ding_test",
        dingtalk_client_secret="secret",
        dingtalk_robot_code="robot_test",
        wecom_corp_id="ww_test",
        wecom_secret="secret",
        wecom_agent_id="1000002",
    )


def account(platform: str) -> AccountContext:
    return AccountContext(
        platform=platform,
        user_id="user-1",
        platform_user_id={"feishu": "ou_1", "dingtalk": "staff_1", "wecom": "zhangsan"}[platform],
        union_id="union_1" if platform == "dingtalk" else None,
        display_name="测试用户",
        access_token="user-token",
    )


@pytest.mark.asyncio
async def test_feishu_reply_update_and_download():
    adapter = FeishuAdapter(settings())
    with respx.mock(assert_all_called=False) as mock:
        token = mock.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
            return_value=httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        )
        reply = mock.post(
            "https://open.feishu.cn/open-apis/im/v1/messages/om_source/reply"
        ).mock(return_value=httpx.Response(200, json={"code": 0, "data": {"message_id": "om_reply"}}))
        patch = mock.patch(
            "https://open.feishu.cn/open-apis/task/v2/tasks/task_guid"
        ).mock(return_value=httpx.Response(200, json={"code": 0, "data": {"task": {"guid": "task_guid"}}}))
        resource = mock.get(
            "https://open.feishu.cn/open-apis/im/v1/messages/om_source/resources/file_key"
        ).mock(return_value=httpx.Response(200, content=b"file-bytes"))

        result = await adapter.reply_text(
            account("feishu"),
            MessageRef(id="m1", platform="feishu", platform_msg_id="om_source", chat_id="oc_1", chat_type="group", sender_platform_id="ou_1"),
            "收到",
        )
        assert result.platform_msg_id == "om_reply"
        assert json.loads(reply.calls[0].request.content)["msg_type"] == "text"

        await adapter.update_task_status(
            account("feishu"), SimpleNamespace(platform_task_id="task_guid"), "done", "token"
        )
        patch_body = json.loads(patch.calls[0].request.content)
        assert patch_body["update_fields"] == ["completed_at"]
        assert patch_body["task"]["completed_at"].isdigit()

        data = await adapter.download_file(
            account("feishu"), SimpleNamespace(message_id="om_source", platform_file_key="file_key")
        )
        assert data == b"file-bytes"
        assert resource.called


@pytest.mark.asyncio
async def test_dingtalk_group_quote_and_todo():
    adapter = DingTalkAdapter(settings())
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.dingtalk.com/v1.0/oauth2/accessToken").mock(
            return_value=httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        )
        quote = mock.post("https://api.dingtalk.com/v1.0/robot/groupMessages/send").mock(
            return_value=httpx.Response(200, json={"processQueryKey": "pq_1"})
        )
        create = mock.post("https://api.dingtalk.com/v1.0/todo/users/union_1/tasks").mock(
            return_value=httpx.Response(200, json={"id": "todo_1"})
        )
        update = mock.put("https://api.dingtalk.com/v1.0/todo/users/union_1/tasks/todo_1").mock(
            return_value=httpx.Response(200, json={"result": True})
        )

        result = await adapter.reply_text(
            account("dingtalk"),
            MessageRef(
                id="m1",
                platform="dingtalk",
                platform_msg_id="msg_1",
                chat_id="cid_1",
                chat_type="group",
                sender_platform_id="sender_open_1",
            ),
            "收到",
        )
        assert result.platform_msg_id == "pq_1"
        body = json.loads(quote.calls[0].request.content)
        assert body["referenceOpenMessageId"] == "msg_1"
        assert body["srcMsgSendOpenDingTalkId"] == "sender_open_1"
        assert body["msgKey"] == "sampleMarkdown"

        ref = await adapter.create_task(
            account("dingtalk"),
            TaskDraft(unified_id="task-1", title="测试待办", description="desc"),
        )
        assert ref.platform_task_id == "todo_1"
        assert json.loads(create.calls[0].request.content)["sourceId"] == "task-1"

        await adapter.update_task_status(
            account("dingtalk"), SimpleNamespace(platform_task_id="todo_1"), "done", "token"
        )
        assert json.loads(update.calls[0].request.content)["done"] is True


@pytest.mark.asyncio
async def test_wecom_todo_update_and_fetch():
    adapter = WeComAdapter(settings())
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "access_token": "token", "expires_in": 7200})
        )
        update = mock.post("https://qyapi.weixin.qq.com/cgi-bin/todo/update").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        )
        get = mock.post("https://qyapi.weixin.qq.com/cgi-bin/todo/get").mock(
            return_value=httpx.Response(
                200,
                json={"errcode": 0, "data_list": [{"todo_id": "todo_1", "todo_status": 0, "content": "测试"}]},
            )
        )

        await adapter.update_task_status(
            account("wecom"), SimpleNamespace(platform_task_id="todo_1"), "done", "token"
        )
        body = json.loads(update.calls[0].request.content)
        assert body["todo_id"] == "todo_1"
        assert body["status"] == 0

        state = await adapter.fetch_task(account("wecom"), SimpleNamespace(platform_task_id="todo_1"))
        assert state.status == "done"
        assert state.title == "测试"
        assert get.called


class FakeAibotChannel:
    def __init__(self, frames: dict[str, dict] | None = None) -> None:
        self.frames = frames or {}
        self.replies: list[tuple[str, str]] = []

    def has_frame(self, msgid: str) -> bool:
        return msgid in self.frames

    async def reply_in_conversation(self, msgid: str, text: str) -> None:
        self.replies.append((msgid, text))


def wecom_ref(msgid: str, *, aibot: bool = True) -> MessageRef:
    return MessageRef(
        id="msg-db-1",
        platform="wecom",
        platform_msg_id=msgid,
        chat_id="zhangsan",
        chat_type="direct",
        sender_platform_id="zhangsan",
        sender_name="张三",
        source_raw={"aibotid": "bot_1", "msgid": msgid} if aibot else {},
    )


@pytest.mark.asyncio
async def test_wecom_aibot_reply_stays_in_same_conversation():
    adapter = WeComAdapter(settings())
    channel = FakeAibotChannel({"msg-1": {"body": {"msgid": "msg-1"}}})
    adapter.bind_aibot(channel)

    result = await adapter.reply_text(account("wecom"), wecom_ref("msg-1"), "收到，我来看")

    assert channel.replies == [("msg-1", "收到，我来看")]
    assert result.raw["channel"] == "aibot"


@pytest.mark.asyncio
async def test_wecom_aibot_reply_reports_expired_frame():
    from backend.app.platform.base import PlatformError

    adapter = WeComAdapter(settings())
    adapter.bind_aibot(FakeAibotChannel())

    # No app-message fallback: replying through /message/send would silently
    # move the answer into a different WeCom conversation.
    with pytest.raises(PlatformError) as exc:
        await adapter.reply_text(account("wecom"), wecom_ref("msg-gone"), "在吗")
    assert exc.value.code == "wecom_aibot_frame_missing"


@pytest.mark.asyncio
async def test_wecom_runner_replies_via_cached_frame(app):
    from backend.app.services.runners import WeComRunner

    runner = WeComRunner(app.state.settings, app.state.db, app.state.adapters, app.state.bus, "user-1")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, dict]] = []

        async def reply(self, frame, body):
            self.calls.append((frame, body))
            return {"errcode": 0}

    runner.client = FakeClient()
    frame = {"headers": {"req_id": "req-1"}, "body": {"msgid": "msg-1", "msgtype": "text"}}
    runner.remember_frame("msg-1", frame)

    assert runner.has_frame("msg-1")
    await runner.reply_in_conversation("msg-1", "好的")

    assert runner.client.calls[0][0] is frame
    assert runner.client.calls[0][1]["text"]["content"] == "好的"
    assert runner.status["replies"] == 1


def test_wecom_runner_caps_cached_frames(app):
    from backend.app.services.runners import WeComRunner

    runner = WeComRunner(app.state.settings, app.state.db, app.state.adapters, app.state.bus, "user-1")
    for index in range(runner.MAX_CACHED_FRAMES + 10):
        runner.remember_frame(f"m{index}", {"body": {"msgid": f"m{index}"}})

    assert len(runner.frames) == runner.MAX_CACHED_FRAMES
    assert not runner.has_frame("m0")
    assert runner.has_frame(f"m{runner.MAX_CACHED_FRAMES + 9}")


@pytest.mark.asyncio
async def test_wecom_aibot_binds_operator_identity_once(app):
    from sqlalchemy import select

    from backend.app.models import User
    from backend.app.services.accounts import bind_wecom_aibot_operator, get_account

    async with app.state.db.session() as session:
        user = (await session.scalars(select(User))).first()
        assert user is not None

        # The demo seed already links a WeCom account; aibot binding only fills
        # the gap when the operator has no WeCom identity yet.
        seeded = await get_account(session, user.id, "wecom")
        if seeded is not None:
            await session.delete(seeded)
            await session.commit()

        first = await bind_wecom_aibot_operator(
            session,
            owner_id=user.id,
            sender_id="zhangsan",
            sender_name="张三",
            settings=app.state.settings,
        )
        again = await bind_wecom_aibot_operator(
            session,
            owner_id=user.id,
            sender_id="lisi",
            sender_name="李四",
            settings=app.state.settings,
        )
        assert first is True
        assert again is False

        account = await get_account(session, user.id, "wecom")
        assert account is not None
        assert account.platform_user_id == "zhangsan"
