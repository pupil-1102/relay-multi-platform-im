from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings

BASE = "https://open.feishu.cn/open-apis"


def show(label: str, resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    code = body.get("code", resp.status_code)
    msg = body.get("msg", "")
    print(f"[{label}] HTTP {resp.status_code} code={code} msg={msg}")
    return body


async def main() -> None:
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        print("missing FEISHU_APP_ID/SECRET")
        return
    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        token_body = show("tenant_token", token_resp)
        token = token_body.get("tenant_access_token")
        if not token:
            print("RESULT: tenant token failed; check app id/secret and app status")
            return
        print(f"[tenant_token] expire={token_body.get('expire')}")
        headers = {"Authorization": f"Bearer {token}"}

        bot = await client.get(f"{BASE}/bot/v3/info", headers=headers)
        bot_body = show("bot_info", bot)
        bot_data = bot_body.get("bot", {})
        print(
            "[bot_info]",
            json.dumps(
                {
                    "app_name": bot_data.get("app_name"),
                    "open_id": bot_data.get("open_id"),
                    "activate_status": bot_data.get("activate_status"),
                    "ip_white_list": bot_data.get("ip_white_list"),
                },
                ensure_ascii=False,
            ),
        )

        app = await client.get(
            f"{BASE}/application/v6/applications/{settings.feishu_app_id}",
            headers=headers,
            params={"lang": "zh_cn"},
        )
        app_body = show("app_info", app)
        app_data = app_body.get("data", {}).get("app", {})
        if app_data:
            print(
                "[app_info]",
                json.dumps(
                    {
                        "app_name": app_data.get("app_name"),
                        "status": app_data.get("status"),
                        "scopes": app_data.get("scopes"),
                        "bot": app_data.get("bot"),
                    },
                    ensure_ascii=False,
                )[:3000],
            )

        chats = await client.get(f"{BASE}/im/v1/chats", headers=headers, params={"page_size": 20})
        chats_body = show("chats", chats)
        items = chats_body.get("data", {}).get("items", [])
        print("[chats] count=", len(items))
        for item in items[:10]:
            print("[chat]", item.get("chat_id"), item.get("name"), item.get("chat_mode"))

        task_guid = None
        task = await client.post(
            f"{BASE}/task/v2/tasks",
            headers={**headers, "Content-Type": "application/json; charset=utf-8"},
            json={
                "summary": "Relay 试运行任务（可删除）",
                "description": "由 Relay 配置检查脚本创建，用于验证 task:task:write。",
                "extra": "relay-feishu-trial",
            },
        )
        task_body = show("task_create", task)
        task_data = task_body.get("data", {}).get("task", {})
        task_guid = task_data.get("guid")
        if task_guid:
            print("[task_create] guid=", task_guid, "status=", task_data.get("status"))
            deleted = await client.delete(f"{BASE}/task/v2/tasks/{task_guid}", headers=headers)
            show("task_delete", deleted)
        else:
            print("RESULT: task write not available; task:task:write or app task permission is missing")

        print("RESULT: tenant token + bot info above; interpret missing scopes from error codes.")


def ws_probe() -> None:
    settings = get_settings()
    errors: list[str] = []
    try:
        import lark_oapi as lark
    except ImportError as exc:
        print("[ws] lark-oapi missing:", exc)
        return

    def handler(_data) -> None:
        return None

    dispatcher = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handler)
        .build()
    )
    client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=dispatcher,
        log_level=lark.LogLevel.WARNING,
    )

    def run() -> None:
        try:
            client.start()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    thread = threading.Thread(target=run, daemon=True, name="feishu-ws-probe")
    thread.start()
    time.sleep(6)
    try:
        client.stop()
    except Exception:  # noqa: BLE001
        pass
    thread.join(timeout=3)
    if errors:
        print("[ws] error:", errors[-1][:500])
    else:
        print("[ws] client started/stopped for 6s without exception (connection readiness needs the app's 长连接 mode enabled)")


if __name__ == "__main__":
    asyncio.run(main())
    ws_probe()
