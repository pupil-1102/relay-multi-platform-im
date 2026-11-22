from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import Platform, TaskLink, TaskStatus
from ..platform.base import AccountContext
from ..db import utcnow

ECHO_TTL_SECONDS = 180


def to_platform_status(platform: str, status: str) -> str:
    """Unified -> platform-native status token (used for echo comparison)."""
    if status == TaskStatus.DONE:
        return "done"
    if status == TaskStatus.IN_PROGRESS:
        return "in_progress"
    return "open"


def from_platform_status(platform: str, raw_status: str | None) -> str:
    value = (raw_status or "").lower()
    if platform == Platform.FEISHU:
        if value in {"done", "completed", "true"} or value.isdigit() and value not in {"0", ""}:
            return TaskStatus.DONE
        return TaskStatus.OPEN
    if platform == Platform.DINGTALK:
        if value in {"done", "completed", "2", "true"}:
            return TaskStatus.DONE
        if value in {"1", "in_progress"}:
            return TaskStatus.IN_PROGRESS
        return TaskStatus.OPEN
    if platform == Platform.WECOM:
        if value in {"0", "done", "completed"}:
            return TaskStatus.DONE
        if value in {"1", "in_progress"}:
            return TaskStatus.IN_PROGRESS
        return TaskStatus.OPEN
    return TaskStatus.OPEN


def prepare_write(link: TaskLink, status: str, ttl: int = ECHO_TTL_SECONDS) -> str:
    token = uuid.uuid4().hex
    link.sync_token = token
    link.expected_status = status
    link.expected_until = utcnow() + timedelta(seconds=ttl)
    return token


def is_echo(link: TaskLink, status: str, event_time: datetime | None = None) -> bool:
    if not link.sync_token or not link.expected_status:
        return False
    if link.expected_status != status:
        return False
    if link.expected_until is None or link.expected_until < datetime.now(UTC):
        return False
    if event_time and link.last_synced_at and event_time < link.last_synced_at - timedelta(seconds=5):
        return False
    return True


def consume_token(link: TaskLink) -> None:
    link.sync_token = None
    link.expected_status = None
    link.expected_until = None


def account_from_orm(account: Any, *, access_token: str | None = None) -> AccountContext:
    return AccountContext(
        platform=account.platform,
        user_id=account.user_id,
        platform_user_id=account.platform_user_id,
        union_id=account.union_id,
        display_name=account.display_name,
        access_token=access_token,
        refresh_token=None,
    )


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:8]
