"""Minimal asyncio IRC client for driving test scenarios against ircu2."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from pybot.irc.protocol import Message, format_line, parse_message

log = logging.getLogger("pybot.tests.fake_client")


@dataclass
class FakeClient:
    host: str
    port: int
    nick: str
    username: str = "fake"
    realname: str = "fake client"
    password: str | None = None
    use_cap: bool = False

    reader: asyncio.StreamReader | None = field(default=None, init=False, repr=False)
    writer: asyncio.StreamWriter | None = field(default=None, init=False, repr=False)
    messages: list[Message] = field(default_factory=list, init=False)
    raw_lines: list[str] = field(default_factory=list, init=False)
    registered: bool = field(default=False, init=False)
    _read_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _waiters: list[tuple[Callable[[Message], bool], asyncio.Future[Message]]] = field(
        default_factory=list, init=False, repr=False
    )

    async def connect(self, timeout: float = 15.0) -> None:
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=timeout,
        )
        self._read_task = asyncio.create_task(self._read_loop(), name=f"fake-{self.nick}")
        if self.password:
            await self.send("PASS", self.password)
        if self.use_cap:
            await self.send("CAP", "LS", "302")
        await self.send("NICK", self.nick)
        await self.send("USER", self.username, "0", "*", self.realname)
        if self.use_cap:
            await self.send("CAP", "END")
        await self.wait_for(lambda m: m.command == "001", timeout=timeout)
        # 001 first param is the assigned nick
        welcome = next(m for m in self.messages if m.command == "001")
        if welcome.params:
            self.nick = welcome.params[0]
        self.registered = True
        log.info("FakeClient %s registered", self.nick)

    async def _read_loop(self) -> None:
        assert self.reader is not None
        try:
            while True:
                data = await self.reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                self.raw_lines.append(line)
                msg = parse_message(line)
                if msg.command == "PING":
                    await self.send("PONG", msg.trailing or msg.params[0])
                    continue
                self.messages.append(msg)
                pending = []
                for pred, fut in self._waiters:
                    if fut.done():
                        continue
                    if pred(msg):
                        fut.set_result(msg)
                    else:
                        pending.append((pred, fut))
                self._waiters = pending
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("FakeClient %s read error", self.nick)

    async def send(self, command: str, *params: str) -> None:
        if not self.writer:
            raise RuntimeError("not connected")
        line = format_line(command, *params)
        self.writer.write((line + "\r\n").encode("utf-8"))
        await self.writer.drain()

    async def join(self, channel: str) -> None:
        await self.send("JOIN", channel)
        await self.wait_for(
            lambda m: m.command == "JOIN"
            and (m.source_nick or "").lower() == self.nick.lower()
            and channel.lower() in " ".join(m.params).lower(),
            timeout=10,
        )

    async def part(self, channel: str, reason: str = "") -> None:
        if reason:
            await self.send("PART", channel, reason)
        else:
            await self.send("PART", channel)

    async def privmsg(self, target: str, text: str) -> None:
        await self.send("PRIVMSG", target, text)

    async def mode(self, target: str, *args: str) -> None:
        await self.send("MODE", target, *args)

    async def nick_change(self, new_nick: str) -> None:
        await self.send("NICK", new_nick)
        await self.wait_for(
            lambda m: m.command == "NICK"
            and (m.params[0] if m.params else m.trailing) == new_nick,
            timeout=10,
        )
        self.nick = new_nick

    async def quit(self, message: str = "bye") -> None:
        try:
            await self.send("QUIT", message)
        except Exception:
            pass
        await self.close()

    async def close(self) -> None:
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None
        self.reader = None
        self._read_task = None

    async def wait_for(
        self,
        predicate: Callable[[Message], bool],
        timeout: float = 10.0,
    ) -> Message:
        for msg in self.messages:
            if predicate(msg):
                return msg
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Message] = loop.create_future()
        self._waiters.append((predicate, fut))
        return await asyncio.wait_for(fut, timeout=timeout)

    def messages_of(self, command: str) -> list[Message]:
        return [m for m in self.messages if m.command == command]
