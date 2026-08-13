"""WHO / WHOX queries and polling."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pybot.irc.client import IRCClient

log = logging.getLogger("pybot.irc.who")

# Field order for 354 replies — must match request flag order.
# Keep realname (r) last because it can contain spaces and must be trailing.
# t c u h n a o r → type channel user host nick account flags realname
WHOX_FLAGS = "tcuhnaor"


class WhoManager:
    def __init__(self, client: IRCClient) -> None:
        self.client = client
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._current: str | None = None
        self._done: asyncio.Event | None = None
        self._poll_handle = None

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="who-worker")

    async def stop(self) -> None:
        self.stop_poll()
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None

    async def query(self, target: str) -> None:
        """Enqueue a WHO/WHOX for target (channel or nick)."""
        self.start()
        await self._queue.put(target)

    async def _run(self) -> None:
        while True:
            target = await self._queue.get()
            try:
                await self._do_query(target)
            except Exception:
                log.exception("WHO query failed for %s", target)
            finally:
                self._queue.task_done()

    async def _do_query(self, target: str) -> None:
        self._current = target
        self._done = asyncio.Event()
        if self.client.isupport.whox:
            # No token — queries are serialized
            await self.client.send("WHO", target, f"%{WHOX_FLAGS}")
        else:
            await self.client.send("WHO", target)
        try:
            await asyncio.wait_for(self._done.wait(), timeout=30)
        except asyncio.TimeoutError:
            log.warning("WHO timed out for %s", target)
        finally:
            self._current = None
            self._done = None

    def on_end(self, target: str | None = None) -> None:
        if self._done and not self._done.is_set():
            self._done.set()

    def needs_account_poll(self) -> bool:
        caps = self.client.caps
        has_account_events = caps.has("account-notify") or caps.has("account-tag")
        has_extjoin = caps.has("extended-join")
        return not (has_account_events or has_extjoin)

    def start_poll(self, interval: float, channels_fn) -> None:
        """Poll WHO for all joined channels when account caps missing."""
        self.stop_poll()
        if interval <= 0:
            return

        async def _tick() -> None:
            if not self.needs_account_poll():
                return
            for ch in channels_fn():
                await self.query(ch)

        timers = self.client.timers
        if timers is None:
            return
        self._poll_handle = timers.every(
            interval,
            _tick,
            name="who_poll",
            owner="core:who_poll",
        )
        log.info("WHO poll started every %ss (account caps missing)", interval)

    def stop_poll(self) -> None:
        if self._poll_handle is not None and self.client.timers:
            self.client.timers.cancel(self._poll_handle)
            self._poll_handle = None
