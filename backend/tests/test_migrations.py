from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_fresh_database_migrates_and_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    first = subprocess.run(
        [sys.executable, "-m", "backend.app.migrate"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert first.returncode == 0, first.stderr
    connection = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        version = list(connection.execute("select version_num from alembic_version"))
    finally:
        connection.close()
    assert {"users", "messages", "unified_tasks", "platform_task_links", "files", "alembic_version"} <= tables
    assert version

    second = subprocess.run(
        [sys.executable, "-m", "backend.app.migrate"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert second.returncode == 0, second.stderr


def test_legacy_database_is_stamped_to_its_schema_and_upgraded(tmp_path: Path):
    """A pre-Alembic DB must not be stamped to head: newer columns would be skipped."""
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE users (
                id VARCHAR(32) PRIMARY KEY,
                email VARCHAR(255) UNIQUE,
                display_name VARCHAR(120),
                avatar_url VARCHAR(500),
                is_placeholder BOOLEAN,
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE TABLE conversation_reads (
                id VARCHAR(32) PRIMARY KEY,
                owner_id VARCHAR(32),
                platform VARCHAR(20),
                platform_chat_id VARCHAR(300),
                last_read_at DATETIME
            );
            CREATE TABLE messages (
                id VARCHAR(32) PRIMARY KEY,
                owner_id VARCHAR(32),
                platform VARCHAR(20),
                text TEXT
            );
            INSERT INTO users (id, email, display_name, is_placeholder)
            VALUES ('u1', 'legacy@example.com', '老用户', 0);
            """
        )
        connection.commit()
    finally:
        connection.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "backend.app.migrate"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "stamped 9f2c1b7a4d6e" in result.stdout

    connection = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        user_columns = {row[1] for row in connection.execute("pragma table_info(users)")}
        version = list(connection.execute("select version_num from alembic_version"))[0][0]
        email = list(connection.execute("select email from users"))
    finally:
        connection.close()

    assert {"platform_configs", "auto_reply_rules"} <= tables
    assert "password_hash" in user_columns
    assert version == "e3f4a5b6c7d8"
    assert email == [("legacy@example.com",)], "existing rows must survive the upgrade"
