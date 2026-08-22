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
        if not self._irc_connected():
            self.log.warning("Skipping PRIVMSG to %s: IRC disconnected", target)
            return
        await self._bot.irc.privmsg(target, text)

    async def notice(self, target: str, text: str) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping NOTICE to %s: IRC disconnected", target)
            return
        await self._bot.irc.notice(target, text)

    async def join(self, channel: str, key: str | None = None) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping JOIN %s: IRC disconnected", channel)
            return
        await self._bot.irc.join(channel, key)

    async def part(self, channel: str, message: str | None = None) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping PART %s: IRC disconnected", channel)
            return
        await self._bot.irc.part(channel, message)

    async def mode(self, target: str, *args: str) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping MODE on %s: IRC disconnected", target)
            return
        await self._bot.irc.mode(target, *args)

    async def kick(self, channel: str, nick: str, reason: str | None = None) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping KICK %s from %s: IRC disconnected", nick, channel)
            return
        await self._bot.irc.kick(channel, nick, reason)

    async def raw(self, command: str, *params: str) -> None:
        if not self._irc_connected():
            self.log.warning("Skipping raw %s: IRC disconnected", command)
            return
        await self._bot.irc.send(command, *params)

    async def oper(
        self, name: str, password: str, timeout: float = 10.0
    ) -> tuple[bool, str]:
        return await self._bot.irc.oper(name, password, timeout=timeout)

    async def stats(
        self, letter: str, timeout: float = 10.0
    ) -> list[tuple[int, list[str]]]:
        return await self._bot.irc.stats(letter, timeout=timeout)

    async def shutdown(self, reason: str, exit_code: int = 1) -> None:
        """Request a process exit code, then stop the bot."""
        self._bot.request_exit_code(exit_code)
        await self._bot.stop(reason)

    async def who(self, target: str) -> None:
        await self._bot.irc.who.query(target)

    def _irc_connected(self) -> bool:
        conn = getattr(self._bot.irc, "conn", None)
        return bool(conn and conn.connected)

    def is_registered(self) -> bool:
        """True once 001 has been received on the current IRC connection.

        A module's own `registered` event handler fires exactly once per
        connection — never again on a hot config/module reload, since that
        tears down and recreates module instances without touching the IRC
        session. A module whose startup work is gated behind `registered`
        must check this in `setup()` too, or it goes dormant after every
        reload until the next real disconnect/reconnect.
        """
        return bool(self._bot.irc.registered)

    def get_user(self, nick: str) -> User | None:
        return self._bot.irc.state.get_user(nick)

    def get_channel(self, name: str) -> Channel | None:
        return self._bot.irc.state.get_channel(name)

    def get_members(self, channel: str) -> list[str]:
        ch = self._bot.irc.state.get_channel(channel)
        if not ch:
            return []
        return [m.nick for m in ch.members.values()]

    def get_own_nick(self) -> str:
        """The bot's current nick (may differ from the configured one)."""
        return self._bot.irc.nick

    def casefold(self, name: str) -> str:
        return self._bot.irc.isupport.casefold(name)

    def nicks_equal(self, a: str, b: str) -> bool:
        return self._bot.irc.isupport.equal(a, b)

    def is_channel_op(self, channel: str, nick: str) -> bool:
        ch = self.get_channel(channel)
        if not ch:
            return False
        member = ch.members.get(self.casefold(nick))
        return bool(member and "o" in member.prefixes)

    def on(self, event: str, handler: Callable[..., Awaitable[None] | None]) -> None:
        self._bot.bus.on(event, handler, owner=self.owner)

    def off(self, event: str, handler: Callable[..., Awaitable[None] | None] | None = None) -> None:
        self._bot.bus.off(event, handler, owner=self.owner)

    def register_channels(self, channels: list[Any] | None = None) -> None:
        self._bot.register_wanted_channels(self.owner, channels)

    def unregister_channels(self) -> None:
        self._bot.unregister_wanted_channels(self.owner)

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

    async def mount_route(
        self,
        method: str,
        path: str,
        handler: Callable[..., Awaitable[Any]],
    ) -> None:
        # The HTTP server only binds a socket once something actually needs
        # it — start() is idempotent, so this is a no-op on every mount after
        # the first.
        self._bot.http.mount(method, path, handler, owner=self.owner)
        await self._bot.http.start()

    def unmount_routes(self) -> None:
        self._bot.http.unmount_owner(self.owner)
