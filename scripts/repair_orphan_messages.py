"""Repair outbound messages that were delivered without a conversation key.

Older builds could send a message successfully and then reject the
"start conversation" request, leaving ``platform_chat_id`` NULL.  The inbox
filters those rows out, so the message looked lost even though the platform
had delivered it.

Dry run by default; pass ``--apply`` to write the repair.  WeCom and Feishu
direct chats are keyed by the peer handle, so they can be repaired safely.
DingTalk rows are only reported: a 1:1 DingTalk conversation is keyed by
``cid…`` and a bare staff userId would create a second, wrong conversation.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\repair_orphan_messages.py
    .\\.venv\\Scripts\\python.exe scripts\\repair_orphan_messages.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.models import Message  # noqa: E402

SAFE_PLATFORMS = {"wecom", "feishu"}


async def run(apply: bool) -> int:
    settings = get_settings()
    db = Database(settings)
    await db.init()
    try:
        async with db.session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Message)
                        .where(
                            Message.direction == "outbound",
                            Message.platform_chat_id.is_(None),
                            Message.receiver_platform_id.is_not(None),
                        )
                        .order_by(Message.created_at)
                    )
                ).all()
            )
            repair = [row for row in rows if row.platform in SAFE_PLATFORMS]
            review = [row for row in rows if row.platform not in SAFE_PLATFORMS]

            print(f"database: {settings.database_url}")
            print(f"orphan outbound messages: {len(rows)} (repairable {len(repair)}, review {len(review)})")
            for row in repair:
                print(f"  repair {row.platform:8} {row.created_at} -> {row.receiver_platform_id} | {row.text[:40]}")
            for row in review:
                print(f"  review {row.platform:8} {row.created_at} receiver={row.receiver_platform_id} | {row.text[:40]}")

            if apply:
                for row in repair:
                    row.platform_chat_id = row.receiver_platform_id
                await session.commit()
                print(f"applied: {len(repair)} row(s) updated")
            elif repair:
                print("dry run: pass --apply to update these rows")
            return 0
    finally:
        await db.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair instead of a dry run")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
