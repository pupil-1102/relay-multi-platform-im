from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from ..platform.base import PlatformError

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RateLimiter:
    """Serialized min-interval limiter; deliberately simple and predictable."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(min_interval, 0.0)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class PlatformGate:
    """Rate limit every platform call; retry only idempotent operations.

    Reply/send/create-task are intentionally never retried because the platform
    APIs do not offer an idempotency key — a retry could duplicate a user-visible
    message or task. Status updates, reads and downloads are safe to retry.
    """

    def __init__(self, intervals: dict[str, float] | None = None) -> None:
        self._limiters = {
            platform: RateLimiter(interval)
            for platform, interval in (
                intervals
                or {
                    "feishu": 0.08,
                    "dingtalk": 0.20,
                    "wecom": 2.50,
                }
            ).items()
        }
        self._default = RateLimiter(0.20)

    def _limiter(self, platform: str) -> RateLimiter:
        return self._limiters.get(platform, self._default)

    async def _call(self, platform: str, fn: Callable[[], Awaitable[T]], *, retry: bool) -> T:
        attempts = 3 if retry else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            await self._limiter(platform).wait()
            try:
                return await fn()
            except PlatformError as exc:
                last_error = exc
                if not retry or not exc.retryable or attempt == attempts - 1:
                    raise
                delay = (0.4 * (2**attempt)) + random.uniform(0, 0.15)
                logger.info(
                    "Retrying idempotent %s call after %s (%s), attempt %s/%s",
                    platform,
                    exc.code,
                    exc.message,
                    attempt + 1,
                    attempts,
                )
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if not retry or attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.4 * (2**attempt))
        raise last_error or RuntimeError("platform call failed")

    async def once(self, platform: str, fn: Callable[[], Awaitable[T]]) -> T:
        return await self._call(platform, fn, retry=False)

    async def idempotent(self, platform: str, fn: Callable[[], Awaitable[T]]) -> T:
        return await self._call(platform, fn, retry=True)


platform_gate = PlatformGate()
