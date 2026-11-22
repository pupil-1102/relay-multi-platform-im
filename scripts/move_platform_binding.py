"""Move one platform identity to another Relay account (operator action).

Useful when an identity was bound to the wrong account: instead of logging in as
the old account to unbind and re-authorising, the operator moves the binding on
the host. Dry run by default.

Examples:
    python scripts/move_platform_binding.py --platform feishu ^
        --handle ou_xxxxxxxx --to-email new@example.com
    python scripts/move_platform_binding.py --platform feishu ^
        --handle ou_xxxxxxxx --to-email new@example.com --apply
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
from backend.app.services.account_data import MigrationError, move_platform_binding  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(settings)
    await db.init()
    try:
        async with db.session() as session:
            target = None
            if args.to_id:
                target = await session.get(User, args.to_id)
            elif args.to_email:
                target = await session.scalar(select(User).where(User.email == args.to_email))
            if target is None:
                print("target account not found (use --to-email or --to-id)")
                return 1
            report = await move_platform_binding(
                session,
                platform=args.platform,
                handle=args.handle,
                target_id=target.id,
                apply=args.apply,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not args.apply and report["status"] == "moved":
                print("\ndry run: pass --apply to move the binding")
            return 0
    except MigrationError as exc:
        print(f"move rejected: {exc}")
        return 1
    finally:
        await db.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=["feishu", "dingtalk", "wecom"])
    parser.add_argument("--handle", required=True, help="platform user id, e.g. ou_xxx / userId / wecom userid")
    parser.add_argument("--to-email", help="Relay account email that should own the identity")
    parser.add_argument("--to-id", help="Relay user id (for accounts without an email)")
    parser.add_argument("--apply", action="store_true", help="write the move (default: dry run)")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
