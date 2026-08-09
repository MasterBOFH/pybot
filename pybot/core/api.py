"""BotAPI — stable surface modules use to talk to core."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, TypeVar

from pybot.core.timers import TimerHandle
from pybot.logging_setup import get_module_logger

if TYPE_CHECKING:
    from aiohttp import web

    from pybot.core.bot import Bot
    from pybot.irc.state import Channel, User

T = TypeVar("T")


class BotAPI:
    def __init__(self, bot: Bot, module_name: str) -> None:
        self._bot = bot
        self.module_name = module_name
        self.owner = f"module:{module_name}"
        self.log: logging.Logger = get_module_logger(module_name)

    def get_config(self) -> dict[str, Any]:
        return dict(self._bot.module_config(self.module_name))

    def get_bot_config(self) -> dict[str, Any]:
        return self._bot.config

    def is_admin(self, payload: dict[str, Any]) -> bool:
        """True if hostmask/account matches irc.admin."""
        return self._bot._is_admin(payload)

    def schedule(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        """Run a coroutine on the bot event loop (safe from other threads)."""
        loop = getattr(self._bot, "loop", None)
        if loop is None or not loop.is_running():
            raise RuntimeError("Bot event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    async def privmsg(self, target: str, text: str) -> None:
        await self._bot.irc.privmsg(target, text)

    async def notice(self, target: str, text: str) -> None:
        await self._bot.irc.notice(target, text)

    async def join(self, channel: str, key: str | None = None) -> None:
        await self._bot.irc.join(channel, key)

    async def part(self, channel: str, message: str | None = None) -> None:
        await self._bot.irc.part(channel, message)

    async def mode(self, target: str, *args: str) -> None:
        await self._bot.irc.mode(target, *args)

    async def who(self, target: str) -> None:
        await self._bot.irc.who.query(target)

    def get_user(self, nick: str) -> User | None:
        return self._bot.irc.state.get_user(nick)

    def get_channel(self, name: str) -> Channel | None:
        return self._bot.irc.state.get_channel(name)

    def get_members(self, channel: str) -> list[str]:
        ch = self._bot.irc.state.get_channel(channel)
        if not ch:
            return []
        return [m.nick for m in ch.members.values()]

    def on(self, event: str, handler: Callable[..., Awaitable[None] | None]) -> None:
        self._bot.bus.on(event, handler, owner=self.owner)

    def off(self, event: str, handler: Callable[..., Awaitable[None] | None] | None = None) -> None:
        self._bot.bus.off(event, handler, owner=self.owner)

    def every(
        self,
        interval: float,
        callback: Callable[[], Awaitable[None] | None],
        *,
        name: str | None = None,
        immediate: bool = False,
    ) -> TimerHandle:
        return self._bot.timers.every(
            interval,
            callback,
            name=name,
            owner=self.owner,
            immediate=immediate,
        )

    def after(
        self,
        delay: float,
        callback: Callable[[], Awaitable[None] | None],
        *,
        name: str | None = None,
    ) -> TimerHandle:
        return self._bot.timers.after(
            delay, callback, name=name, owner=self.owner
        )

    def cancel_timer(self, handle_or_name: TimerHandle | str) -> None:
        self._bot.timers.cancel(handle_or_name)

    def mount_route(
        self,
        method: str,
        path: str,
        handler: Callable[..., Awaitable[Any]],
    ) -> None:
        self._bot.http.mount(method, path, handler, owner=self.owner)

    def unmount_routes(self) -> None:
        self._bot.http.unmount_owner(self.owner)
