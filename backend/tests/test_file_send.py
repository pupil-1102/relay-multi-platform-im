from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
import respx

from backend.app.config import Settings
from backend.app.platform.base import AccountContext, OutboundFile, PlatformError
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
        wecom_aibot_only=False,
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
async def test_feishu_send_file():
    adapter = FeishuAdapter(settings())
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal").mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        )
        upload = mock.post("https://open.feishu.cn/open-apis/im/v1/files").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"file_key": "file_key_1"}})
        )
        send = mock.post("https://open.feishu.cn/open-apis/im/v1/messages").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"message_id": "om_file_1"}})
        )
        result = await adapter.send_file(
            account("feishu"),
            "ou_1",
            OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
        )
        assert result.platform_msg_id == "om_file_1"
        assert upload.called
        body = json.loads(send.calls[0].request.content)
        assert body["msg_type"] == "file"
        assert json.loads(body["content"])["file_key"] == "file_key_1"


@pytest.mark.asyncio
async def test_wecom_send_file():
    adapter = WeComAdapter(settings())
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "access_token": "token", "expires_in": 7200})
        )
        upload = mock.post("https://qyapi.weixin.qq.com/cgi-bin/media/upload").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "media_id": "media_1"})
        )
        send = mock.post("https://qyapi.weixin.qq.com/cgi-bin/message/send").mock(
            return_value=httpx.Response(200, json={"errcode": 0, "msgid": "msg_1"})
        )
        result = await adapter.send_file(
            account("wecom"),
            "zhangsan",
            OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
        )
        assert result.platform_msg_id == "msg_1"
        assert upload.called
        body = json.loads(send.calls[0].request.content)
        assert body["msgtype"] == "file"
        assert body["file"]["media_id"] == "media_1"


@pytest.mark.asyncio
async def test_dingtalk_send_file():
    adapter = DingTalkAdapter(settings())
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.dingtalk.com/v1.0/oauth2/accessToken").mock(
            return_value=httpx.Response(200, json={"accessToken": "token", "expireIn": 7200})
        )
        mock.get("https://oapi.dingtalk.com/gettoken").mock(
            return_value=httpx.Response(200, json={"access_token": "legacy", "expires_in": 7200})
        )
        upload = mock.post("https://oapi.dingtalk.com/media/upload").mock(
            return_value=httpx.Response(200, json={"media_id": "media_1"})
        )
        send = mock.post("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend").mock(
            return_value=httpx.Response(200, json={"processQueryKey": "pq_1"})
        )
        result = await adapter.send_file(
            account("dingtalk"),
            "staff_1",
            OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
        )
        assert result.platform_msg_id == "pq_1"
        assert upload.called
        body = json.loads(send.calls[0].request.content)
        assert body["msgKey"] == "sampleFile"
        assert json.loads(body["msgParam"])["mediaId"] == "media_1"


@pytest.mark.asyncio
async def test_feishu_send_file_as_user_uses_the_operator_token():
    """Personal chats reject the bot (230002), so Relay must speak as the user."""
    adapter = FeishuAdapter(settings())
    with respx.mock(assert_all_called=True) as mock:
        upload = mock.post("https://open.feishu.cn/open-apis/im/v1/files").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"file_key": "file_key_2"}})
        )
        send = mock.post("https://open.feishu.cn/open-apis/im/v1/messages").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {"message_id": "om_file_2"}})
        )
        result = await adapter.send_file(
            account("feishu"),
            "oc_chat_1",
            OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
            as_user=True,
        )
        assert result.platform_msg_id == "om_file_2"
        assert upload.calls[0].request.headers["authorization"] == "Bearer user-token"
        assert send.calls[0].request.headers["authorization"] == "Bearer user-token"


@pytest.mark.asyncio
async def test_feishu_send_file_reports_a_missing_user_scope():
    adapter = FeishuAdapter(settings())
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://open.feishu.cn/open-apis/im/v1/files").mock(
            return_value=httpx.Response(
                400,
                json={"code": 99991679, "msg": "Unauthorized. You do not have permission."},
            )
        )
        with pytest.raises(PlatformError) as excinfo:
            await adapter.send_file(
                account("feishu"),
                "oc_chat_1",
                OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
                as_user=True,
            )
    assert excinfo.value.code == "feishu_user_scope_missing"
    assert "im:resource:upload" in excinfo.value.message


@pytest.mark.asyncio
async def test_feishu_send_file_without_a_user_token_for_a_direct_chat():
    adapter = FeishuAdapter(settings())
    bare = AccountContext(
        platform="feishu",
        user_id="user-1",
        platform_user_id="ou_1",
        display_name="测试用户",
        access_token=None,
    )
    with pytest.raises(PlatformError) as excinfo:
        await adapter.send_file(
            bare,
            "oc_chat_1",
            OutboundFile(filename="hello.txt", mime_type="text/plain", data=b"hello"),
            as_user=True,
        )
    assert excinfo.value.code == "feishu_user_token_missing"


def test_outbound_file_dataclass():
    item = OutboundFile(filename="a.txt", mime_type="text/plain", data=b"x")
    assert item.filename == "a.txt"
