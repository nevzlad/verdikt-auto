"""Rate limiter — prevents API abuse and Telegram blocks."""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter for API calls."""

    def __init__(self, requests_per_minute: int = 30, max_concurrent: int = 5) -> None:
        self.rate = requests_per_minute
        self.max_concurrent = max_concurrent
        self._tokens = requests_per_minute
        self._last_refill = time.monotonic()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._semaphore:
            await self._wait_for_token()

    async def _wait_for_token(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.rate,
                    self._tokens + elapsed * (self.rate / 60.0),
                )
                self._last_refill = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

            await asyncio.sleep(0.1)


class TieredRateLimiter:
    """Rate limiter with per-endpoint tiers."""

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}
        self._default_config = {"requests_per_minute": 30, "max_concurrent": 5}

    def register_endpoint(self, name: str, rpm: int = 30, concurrent: int = 5) -> None:
        self._limiters[name] = RateLimiter(requests_per_minute=rpm, max_concurrent=concurrent)

    async def acquire(self, name: str = "default") -> None:
        limiter = self._limiters.get(name)
        if limiter is None:
            limiter = RateLimiter(**self._default_config)
            self._limiters[name] = limiter
        await limiter.acquire()
