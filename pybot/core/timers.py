"""Asyncio timer engine for core and modules."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("pybot.core.timers")

Callback = Callable[[], Awaitable[None] | None]


@dataclass
class TimerHandle:
    id: str
    name: str
    owner: str
    interval: float | None  # None = one-shot
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _cancelled: bool = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class TimerEngine:
    def __init__(self) -> None:
        self._timers: dict[str, TimerHandle] = {}

    def every(
        self,
        interval: float,
        callback: Callback,
        *,
        name: str | None = None,
        owner: str = "",
        immediate: bool = False,
    ) -> TimerHandle:
        handle = self._make_handle(name, owner, interval)
        handle._task = asyncio.create_task(
            self._interval_loop(handle, callback, immediate),
            name=f"timer:{handle.name}",
        )
        self._timers[handle.id] = handle
        return handle

    def after(
        self,
        delay: float,
        callback: Callback,
        *,
        name: str | None = None,
        owner: str = "",
    ) -> TimerHandle:
        handle = self._make_handle(name, owner, None)
        handle._task = asyncio.create_task(
            self._oneshot(handle, delay, callback),
            name=f"timer:{handle.name}",
        )
        self._timers[handle.id] = handle
        return handle

    def _make_handle(
        self, name: str | None, owner: str, interval: float | None
    ) -> TimerHandle:
        tid = uuid.uuid4().hex[:12]
        return TimerHandle(
            id=tid,
            name=name or tid,
            owner=owner,
            interval=interval,
        )

    async def _interval_loop(
        self, handle: TimerHandle, callback: Callback, immediate: bool
    ) -> None:
        try:
            if immediate:
                await self._run(callback, handle)
            assert handle.interval is not None
            while not handle._cancelled:
                await asyncio.sleep(handle.interval)
                if handle._cancelled:
                    break
                await self._run(callback, handle)
        except asyncio.CancelledError:
            pass
        finally:
            self._timers.pop(handle.id, None)

    async def _oneshot(
        self, handle: TimerHandle, delay: float, callback: Callback
    ) -> None:
        try:
            await asyncio.sleep(delay)
            if not handle._cancelled:
                await self._run(callback, handle)
        except asyncio.CancelledError:
            pass
        finally:
            self._timers.pop(handle.id, None)

    async def _run(self, callback: Callback, handle: TimerHandle) -> None:
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("Timer callback failed name=%s owner=%s", handle.name, handle.owner)

    def cancel(self, handle_or_name: TimerHandle | str) -> None:
        if isinstance(handle_or_name, TimerHandle):
            handles = [handle_or_name]
        else:
            handles = [
                h
                for h in self._timers.values()
                if h.name == handle_or_name or h.id == handle_or_name
            ]
        for h in handles:
            h._cancelled = True
            if h._task and not h._task.done():
                h._task.cancel()
            self._timers.pop(h.id, None)

    def cancel_owner(self, owner: str) -> None:
        for h in list(self._timers.values()):
            if h.owner == owner:
                self.cancel(h)

    def cancel_all(self) -> None:
        for h in list(self._timers.values()):
            self.cancel(h)
