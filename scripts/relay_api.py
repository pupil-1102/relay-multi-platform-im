from __future__ import annotations

"""Small local-API probe for Relay.

It mints a session cookie for the single operator account directly from the
database + app security module, so the terminal can read authenticated
endpoints without a browser session.

Never prints secrets: the session token stays in memory only.

Usage:
    python scripts/relay_api.py get /api/realtime/status
    python scripts/relay_api.py get /api/platforms/status
    python scripts/relay_api.py post /api/dingtalk/todo/verify '{"...": ...}'
    python scripts/relay_api.py get /api/conversations --params 'page=1,size=5'
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.models import PlatformAccount, User  # noqa: E402
from backend.app.security import create_session_token  # noqa: E402


async def operator_user_id(db: Database) -> str:
    async with db.session() as session:
        stmt = (
            select(User.id, func.count(PlatformAccount.id).label("accounts"))
            .join(PlatformAccount, PlatformAccount.user_id == User.id, isouter=True)
            .group_by(User.id)
            .order_by(func.count(PlatformAccount.id).desc(), User.created_at.asc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
    if row is None:
        raise SystemExit("no user in database; open the app and log in once")
    return str(row[0])


def parse_params(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            out[key.strip()] = value.strip()
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Relay local API probe (no secrets printed)")
    parser.add_argument("method", choices=["get", "post", "delete"])
    parser.add_argument("path")
    parser.add_argument("body", nargs="?", help="JSON body for post")
    parser.add_argument("--params", help="query params as k=v,k=v")
    parser.add_argument("--base", default=None)
    args = parser.parse_args()

    settings = get_settings()
    base = args.base or f"http://{settings.host}:{settings.port}"
    db = Database(settings)
    try:
        user_id = await operator_user_id(db)
    finally:
        await db.dispose()

    cookies = {settings.session_cookie: create_session_token(user_id, settings)}
    payload = json.loads(args.body) if args.body else None

    async with httpx.AsyncClient(timeout=60, cookies=cookies) as client:
        response = await client.request(
            args.method.upper(),
            f"{base}{args.path}",
            params=parse_params(args.params),
            json=payload,
        )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    print(f"HTTP {response.status_code}")
    print(json.dumps(body, ensure_ascii=False, indent=2) if not isinstance(body, str) else body[:4000])
    return 0 if response.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
