"""Unit tests for TimerEngine and TokenBucket."""

from __future__ import annotations

import asyncio

import pytest

from pybot.core.timers import TimerEngine
from pybot.irc.flood import TokenBucket


@pytest.mark.asyncio
async def test_timer_after_and_cancel_owner() -> None:
    te = TimerEngine()
    hits: list[int] = []

    async def tick() -> None:
        hits.append(1)

    te.after(0.05, tick, name="once", owner="module:t")
    await asyncio.sleep(0.15)
    assert hits == [1]

    hits.clear()
    te.every(0.05, tick, name="loop", owner="module:t", immediate=True)
    await asyncio.sleep(0.12)
    te.cancel_owner("module:t")
    n = len(hits)
    await asyncio.sleep(0.12)
    assert len(hits) == n


@pytest.mark.asyncio
async def test_token_bucket_acquire() -> None:
    tb = TokenBucket(burst=2, rate=100.0)
    await tb.acquire()
    await tb.acquire()
