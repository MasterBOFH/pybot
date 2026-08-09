"""Async event bus with isolated handler failures."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("pybot.core.events")

Handler = Callable[..., Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)

    def on(self, event: str, handler: Handler, *, owner: str = "") -> None:
        self._handlers[event].append((owner, handler))

    def off(
        self,
        event: str,
        handler: Handler | None = None,
        *,
        owner: str | None = None,
    ) -> None:
        if event not in self._handlers:
            return
        if handler is None and owner is None:
            del self._handlers[event]
            return
        self._handlers[event] = [
            (own, h)
            for own, h in self._handlers[event]
            if not (
                (owner is not None and own == owner)
                or (handler is not None and h is handler)
            )
        ]
        if not self._handlers[event]:
            del self._handlers[event]

    def off_owner(self, owner: str) -> None:
        for event in list(self._handlers):
            self._handlers[event] = [
                (own, h) for own, h in self._handlers[event] if own != owner
            ]
            if not self._handlers[event]:
                del self._handlers[event]

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        handlers = list(self._handlers.get(event, []))
        handlers.extend(self._handlers.get("*", []))
        for owner, handler in handlers:
            try:
                result = handler(**payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("Handler error for event=%s owner=%s", event, owner)

    def clear(self) -> None:
        self._handlers.clear()
