"""Token-bucket rate limiter for outbound IRC lines."""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Async token bucket: await acquire() before each send."""

    def __init__(self, burst: float = 5.0, rate: float = 1.0) -> None:
        self._burst = max(1.0, float(burst))
        self._rate = max(0.01, float(rate))
        self._tokens = self._burst
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def configure(self, burst: float | None = None, rate: float | None = None) -> None:
        if burst is not None:
            self._burst = max(1.0, float(burst))
        if rate is not None:
            self._rate = max(0.01, float(rate))
        self._tokens = min(self._tokens, self._burst)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                need = tokens - self._tokens
                wait = need / self._rate
            await asyncio.sleep(wait)
