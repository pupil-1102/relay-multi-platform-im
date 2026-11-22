from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import Settings

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
MIN_PASSWORD_LENGTH = 8


def _fernet(settings: Settings) -> Fernet:
    if settings.encryption_key:
        key = settings.encryption_key.encode()
    else:
        digest = hashlib.sha256(settings.secret_key.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str | None, settings: Settings) -> str | None:
    if not value:
        return None
    return _fernet(settings).encrypt(value.encode()).decode()


def decrypt_secret(value: str | None, settings: Settings) -> str | None:
    if not value:
        return None
    try:
        return _fernet(settings).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def create_session_token(user_id: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.session_ttl_seconds)).timestamp()),
        "aud": "relay-web",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_session_token(token: str, settings: Settings) -> str | None:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience="relay-web",
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a per-user random salt.

    Format: ``pbkdf2_sha256$<iterations>$<salt-b64>$<digest-b64>`` so the
    iteration count can be raised later without invalidating old hashes.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, iterations_raw, salt_raw, digest_raw = stored.split("$")
    except ValueError:
        return False
    if scheme != PASSWORD_SCHEME:
        return False
    try:
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
    except (ValueError, TypeError):
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)
