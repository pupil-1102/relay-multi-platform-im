from __future__ import annotations

import uuid
from pathlib import Path

from backend.app.config import Settings
from backend.app.services.files import content_disposition, is_previewable, save_bytes

SCRATCH = Path(__file__).resolve().parents[2] / ".tmp"


def _settings() -> Settings:
    return Settings(file_dir=SCRATCH / f"file-sec-{uuid.uuid4().hex[:6]}")


def test_svg_is_not_served_inline():
    """An SVG can carry script; inline on the app origin it would run with the session."""
    assert is_previewable("image/svg+xml") is False
    assert is_previewable("image/svg+xml; charset=utf-8") is False
    assert is_previewable("image/png") is True
    assert is_previewable("text/plain") is True


def test_content_disposition_cannot_inject_headers():
    header = content_disposition('a"b\r\nSet-Cookie: x=1.svg', inline=True)
    assert "\r" not in header and "\n" not in header
    assert header.startswith("inline; filename*=UTF-8''")


def test_upload_suffix_cannot_escape_the_file_dir():
    settings = _settings()
    key, digest, size = save_bytes(settings, b"hello", "..\\..\\evil.txt")
    stored = (settings.file_dir / key).resolve()
    assert stored.is_file()
    assert settings.file_dir.resolve() in stored.parents
    assert size == 5
