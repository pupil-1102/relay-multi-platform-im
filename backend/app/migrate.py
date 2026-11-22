from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, make_url

from .config import BASE_DIR, Settings, get_settings

ALEMBIC_INI = BASE_DIR / "alembic.ini"
MIGRATIONS_DIR = BASE_DIR / "backend" / "migrations"

# Legacy (pre-Alembic) databases were created by ``create_all`` at whatever
# revision was current when they were built, so stamping "head" would skip the
# columns added afterwards. Detect what the schema actually contains instead.
_SCHEMA_MARKERS: list[tuple[str, str]] = [
    ("9f2c1b7a4d6e", "conversation_reads"),
    ("b7d4e9c2a1f3", "users.password_hash"),
    ("c1f2a3b4d5e6", "platform_configs"),
    ("d2e3f4a5b6c7", "auto_reply_rules"),
    ("e3f4a5b6c7d8", "auto_reply_rules.attachment_file_id"),
]
_INITIAL_REVISION = "3cd4ac9f2b8d"


def _sync_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        return database_url.replace("+aiosqlite", "")
    return url.set(drivername=url.drivername.split("+", 1)[0]).render_as_string(hide_password=False)


def detect_legacy_revision(engine: Engine) -> str:
    """Best-effort revision that matches an existing pre-Alembic schema."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    columns: dict[str, set[str]] = {}
    for table in ("users", "messages"):
        if table in tables:
            columns[table] = {column["name"] for column in inspector.get_columns(table)}

    revision = _INITIAL_REVISION
    for marker_revision, marker in _SCHEMA_MARKERS:
        table, _, column = marker.partition(".")
        present = table in tables and (not column or column in columns.get(table, set()))
        if not present:
            break
        revision = marker_revision
    return revision


def run_migrations(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    settings.ensure_dirs()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

    engine = create_engine(_sync_url(settings.database_url), future=True)
    try:
        tables = set(inspect(engine).get_table_names())
        if "alembic_version" not in tables and "users" in tables:
            # A database created by the pre-Alembic create_all path. Stamp the
            # revision its schema matches, then replay whatever came after it.
            revision = detect_legacy_revision(engine)
            command.stamp(config, revision)
            command.upgrade(config, "head")
            print(f"[relay] legacy schema detected; stamped {revision} and upgraded to head")
        else:
            command.upgrade(config, "head")
            print("[relay] database upgraded to alembic head")
    finally:
        engine.dispose()


def main() -> int:
    try:
        run_migrations()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[relay] migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
