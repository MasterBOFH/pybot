"""TCP/TLS IRC connection with line framing and token-bucket send."""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pybot.irc.flood import TokenBucket
from pybot.logging_setup import RawLogger

if TYPE_CHECKING:
    pass

log = logging.getLogger("pybot.irc.connection")

LineHandler = Callable[[str], Awaitable[None] | None]


class Connection:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: bool = False,
        tls_verify: bool = True,
        bindhost: str | None = None,
        flood: TokenBucket | None = None,
        raw: RawLogger | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.tls = tls
        self.tls_verify = tls_verify
        self.bindhost = (bindhost or "").strip() or None
        self.flood = flood or TokenBucket()
        self.raw = raw or RawLogger()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._on_line: LineHandler | None = None
        self._on_disconnect: Callable[[Exception | None], Awaitable[None] | None] | None = None
        self.connected = False

    def set_handlers(
        self,
        on_line: LineHandler,
        on_disconnect: Callable[[Exception | None], Awaitable[None] | None] | None = None,
    ) -> None:
        self._on_line = on_line
        self._on_disconnect = on_disconnect

    async def connect(self) -> None:
        ssl_ctx: ssl.SSLContext | bool | None = None
        if self.tls:
            ssl_ctx = ssl.create_default_context()
            if not self.tls_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        local_addr = (self.bindhost, 0) if self.bindhost else None
        log.info(
            "Connecting to %s:%s tls=%s%s",
            self.host,
            self.port,
            self.tls,
            f" bind={self.bindhost}" if self.bindhost else "",
        )
        self._reader, self._writer = await asyncio.open_connection(
            self.host,
            self.port,
            ssl=ssl_ctx,
            local_addr=local_addr,
        )
        self.connected = True
        self._read_task = asyncio.create_task(self._read_loop(), name="irc-read")

    async def _read_loop(self) -> None:
        reader = self._reader
        if reader is None:
            return
        err: Exception | None = None
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                self.raw.inbound(line)
                if self._on_line:
                    result = self._on_line(line)
                    if asyncio.iscoroutine(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err = exc
            log.exception("IRC read loop error")
        finally:
            self.connected = False
            self._reader = None
            if self._on_disconnect:
                result = self._on_disconnect(err)
                if asyncio.iscoroutine(result):
                    await result

    async def send_raw(self, line: str) -> None:
        if not self._writer or not self.connected:
            raise RuntimeError("Not connected")
        await self.flood.acquire()
        payload = (line + "\r\n").encode("utf-8", errors="replace")
        self.raw.outbound(line)
        self._writer.write(payload)
        await self._writer.drain()

    async def close(self) -> None:
        self.connected = False
        writer = self._writer
        self._writer = None
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                try:
                    transport = getattr(writer, "transport", None)
                    if transport is not None:
                        transport.abort()
                except Exception:
                    pass
            except Exception:
                pass

        task = self._read_task
        self._read_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._reader = None
