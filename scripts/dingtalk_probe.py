from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings

BASE = "https://api.dingtalk.com"


async def main() -> None:
    parser = argparse.ArgumentParser(description="DingTalk credential/API diagnostic; never prints secrets")
    parser.add_argument("--unionid", help="optional unionId to resolve userId")
    parser.add_argument("--userid", help="optional userId for a robot text test")
    parser.add_argument("--text", default="Relay 钉钉连通性测试", help="text for --userid")
    args = parser.parse_args()

    settings = get_settings()
    print("app_key_set", bool(settings.dingtalk_client_id))
    print("app_secret_set", bool(settings.dingtalk_client_secret))
    print("robot_code_set", bool(settings.dingtalk_robot_code))
    print("corp_id_set", bool(settings.dingtalk_corp_id))
    print("redirect_uri", settings.dingtalk_redirect_uri)
    print("dws_command", shutil.which(settings.dingtalk_dws_command) or "NOT FOUND")
    print("dws_enabled", settings.dingtalk_dws_enabled)
    print("user_reply_enabled", settings.dingtalk_user_reply_enabled)

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            f"{BASE}/v1.0/oauth2/accessToken",
            json={
                "appKey": settings.dingtalk_client_id,
                "appSecret": settings.dingtalk_client_secret,
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("accessToken")
        print("app_token", token_resp.status_code, "ok" if access_token else token_data)
        if not access_token:
            return

        legacy_resp = await client.get(
            "https://oapi.dingtalk.com/gettoken",
            params={
                "appkey": settings.dingtalk_client_id,
                "appsecret": settings.dingtalk_client_secret,
            },
        )
        legacy_data = legacy_resp.json()
        legacy_token = legacy_data.get("access_token")
        print("legacy_token", legacy_resp.status_code, "ok" if legacy_token else legacy_data.get("errmsg"))
        if not legacy_token:
            return

        if args.unionid:
            by_union = await client.post(
                "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                params={"access_token": legacy_token},
                json={"unionid": args.unionid},
            )
            data = by_union.json()
            user_id = (data.get("result") or {}).get("userid")
            print("unionid_lookup", by_union.status_code, data.get("errcode"), user_id or data.get("errmsg"))
            args.userid = args.userid or user_id

        if args.userid:
            send = await client.post(
                f"{BASE}/v1.0/robot/oToMessages/batchSend",
                headers={"x-acs-dingtalk-access-token": access_token},
                json={
                    "robotCode": settings.dingtalk_robot_code or settings.dingtalk_client_id,
                    "userIds": [args.userid],
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": args.text}, ensure_ascii=False),
                },
            )
            print("robot_send", send.status_code, send.text[:500])


if __name__ == "__main__":
    asyncio.run(main())
