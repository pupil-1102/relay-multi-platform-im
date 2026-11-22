from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import utcnow
from ..models import FileRecord, FileStatus, FileView, Message
from ..platform.registry import AdapterRegistry
from .accounts import context_for_user
from .payloads import file_payload
from .resilience import platform_gate

PREVIEWABLE = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    # image/svg+xml is deliberately absent: an SVG is a script-bearing document
    # and serving it inline on the app origin would let a chat peer run JS with
    # the operator's session. SVGs stay downloadable.
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/json",
}


def _storage_path(settings: Settings, key: str) -> Path:
    return settings.file_dir / key


def save_bytes(settings: Settings, data: bytes, filename: str) -> tuple[str, str, int]:
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(filename).suffix[:20]
    key = f"{digest[:2]}/{digest}{suffix}"
    path = _storage_path(settings, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return key, digest, len(data)


def _detect_type(data: bytes, filename: str, mime: str) -> tuple[str, str]:
    """Fill in missing MIME/filename metadata from bytes.

    WeCom aibot file/image frames only carry a signed URL and aeskey, so the
    original filename is often unavailable. Sniffing makes the preview work.
    """
    if mime and mime != "application/octet-stream":
        return "", ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if data.startswith(b"%PDF"):
        return "application/pdf", ".pdf"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip", ".zip"
    if data.startswith(b"{\\rtf"):
        return "application/rtf", ".rtf"
    return "", ""


async def get_file(session: AsyncSession, file_id: str, owner_id: str) -> FileRecord | None:
    return await session.scalar(
        select(FileRecord).where(FileRecord.id == file_id, FileRecord.owner_id == owner_id)
    )


async def ensure_ready(
    session: AsyncSession,
    *,
    settings: Settings,
    adapters: AdapterRegistry,
    file: FileRecord,
) -> Path:
    if file.storage_key:
        path = _storage_path(settings, file.storage_key)
        if path.exists():
            file.download_status = FileStatus.READY
            return path

    try:
        account = await context_for_user(session, file.owner_id, file.platform, settings)
        adapter = adapters.get(file.platform)
        data = await platform_gate.idempotent(
            file.platform, lambda: adapter.download_file(account, file)
        )
        if len(data) > settings.max_file_bytes:
            raise ValueError(f"文件超过 {settings.max_file_mb}MB 上限")
        mime, extension = _detect_type(data, file.filename, file.mime_type)
        if mime:
            file.mime_type = mime
        if extension and not Path(file.filename).suffix:
            file.filename = f"{file.filename}{extension}"
        key, digest, size = save_bytes(settings, data, file.filename)
        file.storage_key = key
        file.sha256 = digest
        file.size_bytes = size
        file.download_status = FileStatus.READY
        await session.commit()
        return _storage_path(settings, key)
    except Exception:
        file.download_status = FileStatus.FAILED
        await session.commit()
        raise


def _view_map(views: list[FileView]) -> dict[str, FileView]:
    return {view.file_id: view for view in views}


async def file_payloads(
    session: AsyncSession,
    *,
    owner_id: str,
    user_id: str,
    q: str | None = None,
    platform: str | None = None,
    recent: bool = False,
) -> list[dict[str, Any]]:
    stmt = (
        select(FileRecord, Message)
        .outerjoin(Message, FileRecord.message_id == Message.id)
        .where(
            FileRecord.owner_id == owner_id,
            # Files with no message are rule assets (auto-reply attachments);
            # they stay out of the conversation file drawer.
            FileRecord.message_id.is_not(None),
        )
    )
    if platform:
        stmt = stmt.where(FileRecord.platform == platform)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                FileRecord.filename.ilike(like),
                Message.text.ilike(like),
                Message.sender_name.ilike(like),
            )
        )
    if recent:
        stmt = (
            stmt.join(FileView, FileView.file_id == FileRecord.id)
            .where(FileView.user_id == user_id, FileView.open_count > 0)
            .order_by(FileView.last_opened_at.desc())
        )
    else:
        stmt = stmt.order_by(FileRecord.created_at.desc())
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []
    files = [row[0] for row in rows]
    message_map = {row[0].id: row[1] for row in rows}
    views = (
        await session.scalars(
            select(FileView).where(
                FileView.user_id == user_id,
                FileView.file_id.in_([file.id for file in files]),
            )
        )
    ).all()
    vm = _view_map(list(views))
    return [file_payload(file, vm.get(file.id), message_map.get(file.id)) for file in files]


async def record_open(session: AsyncSession, file: FileRecord, user_id: str) -> FileView:
    view = await session.scalar(
        select(FileView).where(FileView.file_id == file.id, FileView.user_id == user_id)
    )
    now = datetime.now(UTC)
    if view is None:
        view = FileView(file_id=file.id, user_id=user_id, first_opened_at=now, last_opened_at=now, open_count=1)
        session.add(view)
    else:
        view.last_opened_at = now
        view.open_count += 1
    await session.commit()
    return view


def content_disposition(filename: str, *, inline: bool) -> str:
    safe = filename.replace('"', "'")
    mode = "inline" if inline else "attachment"
    return f"{mode}; filename*=UTF-8''{quote(safe)}"


def is_previewable(mime_type: str) -> bool:
    return mime_type.split(";")[0].strip().lower() in PREVIEWABLE


def read_stored_bytes(settings: Settings, record: FileRecord) -> bytes | None:
    """Local bytes for a stored file, or None when the blob is missing."""
    if not record.storage_key:
        return None
    try:
        return _storage_path(settings, record.storage_key).read_bytes()
    except OSError:
        return None


__all__ = [
    "ensure_ready",
    "file_payloads",
    "record_open",
    "get_file",
    "save_bytes",
    "content_disposition",
    "is_previewable",
    "read_stored_bytes",
    "utcnow",
]
