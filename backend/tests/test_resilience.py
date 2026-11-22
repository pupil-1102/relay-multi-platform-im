from __future__ import annotations

import asyncio
import time

import pytest

from backend.app.platform.base import PlatformError
from backend.app.services.resilience import PlatformGate, RateLimiter


@pytest.mark.asyncio
async def test_idempotent_call_retries_retryable_error():
    gate = PlatformGate({"feishu": 0})
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PlatformError("temporary", retryable=True)
        return "ok"

    result = await gate.idempotent("feishu", flaky)
    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_once_does_not_retry_non_idempotent_call():
    gate = PlatformGate({"feishu": 0})
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        raise PlatformError("temporary", retryable=True)

    with pytest.raises(PlatformError):
        await gate.once("feishu", flaky)
    assert calls == 1


@pytest.mark.asyncio
async def test_non_retryable_error_is_not_retried():
    gate = PlatformGate({"feishu": 0})
    calls = 0

    async def fails():
        nonlocal calls
        calls += 1
        raise PlatformError("bad request", retryable=False)

    with pytest.raises(PlatformError):
        await gate.idempotent("feishu", fails)
    assert calls == 1


@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(0.05)
    start = time.monotonic()
    await limiter.wait()
    await limiter.wait()
    assert time.monotonic() - start >= 0.045
