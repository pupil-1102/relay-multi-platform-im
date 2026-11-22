"""Migrate history between two Relay accounts (dry run by default).

Examples:
    # preview
    python scripts/migrate_account_data.py --from-email old@example.com --to-email new@example.com
    # move Feishu-only history
    python scripts/migrate_account_data.py --from-email old@example.com --to-email new@example.com --platform feishu --apply

Messages, tasks, files, file views and conversation read state move; platform
bindings do not (use OAuth / unbind for those).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.models import User  # noqa: E402
from backend.app.services.account_data import MigrationError, migrate_owner_data  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings)
    await db.init()
    try:
        async with db.session() as session:
            if args.list:
                for user in (await session.scalars(select(User).order_by(User.created_at))).all():
                    print(f"{user.id}  {user.email!r:28}  {user.display_name}")
                return 0
            source = await _resolve(session, args.from_email, args.from_id, "--from-email/--from-id")
            target = await _resolve(session, args.to_email, args.to_id, "--to-email/--to-id")
            if source is None:
                print("source account not found")
                return 1
            if target is None:
                print("target account not found")
                return 1
            if args.platform and args.platform not in {"feishu", "dingtalk", "wecom"}:
                print(f"unsupported platform: {args.platform}")
                return 1
            report = await migrate_owner_data(
                session,
                source_id=source.id,
                target_id=target.id,
                platform=args.platform,
                apply=args.apply,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not args.apply:
                print("\ndry run: pass --apply to write the changes")
            return 0
    except MigrationError as exc:
        print(f"migration rejected: {exc}")
        return 1
    finally:
        await db.dispose()


async def _resolve(session, email: str | None, user_id: str | None, label: str):
    if user_id:
        return await session.get(User, user_id)
    if email:
        return await session.scalar(select(User).where(User.email == email))
    print(f"missing account selector: provide {label}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-email", help="account that currently owns the history")
    parser.add_argument("--to-email", help="account that should own it")
    parser.add_argument("--from-id", help="source user id (for accounts without an email)")
    parser.add_argument("--to-id", help="target user id")
    parser.add_argument("--platform", choices=["feishu", "dingtalk", "wecom"], default=None)
    parser.add_argument("--apply", action="store_true", help="write the migration (default: dry run)")
    parser.add_argument("--list", action="store_true", help="print accounts (id / email / name) and exit")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
