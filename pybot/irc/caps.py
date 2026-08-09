"""IRCv3 capability negotiation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pybot.irc.client import IRCClient
    from pybot.irc.protocol import Message

log = logging.getLogger("pybot.irc.caps")

DESIRED_CAPS = (
    "sasl",
    "account-tag",
    "account-notify",
    "extended-join",
    "message-tags",
    "server-time",
    "multi-prefix",
    "away-notify",
)


class CapNegotiator:
    def __init__(self, client: IRCClient) -> None:
        self.client = client
        self.available: dict[str, str | None] = {}
        self.acked: set[str] = set()
        self.nakked: set[str] = set()
        self._ls_done = False
        self._finished = False
        self._waiting_sasl = False
        self._req_sent = False

    @property
    def finished(self) -> bool:
        return self._finished

    def has(self, name: str) -> bool:
        return name in self.acked

    def sasl_mechs(self) -> list[str]:
        val = self.available.get("sasl")
        if not val:
            return []
        return [m.strip().upper() for m in val.split(",") if m.strip()]

    async def start(self) -> None:
        await self.client.send("CAP", "LS", "302")

    async def handle(self, msg: Message) -> bool:
        if msg.command != "CAP":
            return False
        if len(msg.params) < 2:
            return True
        sub = msg.params[1].upper()
        rest = msg.params[2:] if len(msg.params) > 2 else []

        if sub == "LS":
            multiline = False
            payload = ""
            if rest and rest[0] == "*":
                multiline = True
                payload = rest[1] if len(rest) > 1 else ""
            elif rest:
                payload = rest[-1]
            self._parse_cap_list(payload)
            if not multiline:
                self._ls_done = True
                await self._request_desired()
        elif sub == "ACK":
            payload = rest[-1] if rest else ""
            for name in payload.split():
                name = name.lstrip("-~")
                self.acked.add(name)
                log.info("CAP ACK %s", name)
            await self._maybe_finish()
        elif sub == "NAK":
            payload = rest[-1] if rest else ""
            for name in payload.split():
                name = name.lstrip("-~")
                self.nakked.add(name)
                log.warning("CAP NAK %s", name)
            await self._maybe_finish()
        elif sub == "NEW":
            payload = rest[-1] if rest else ""
            self._parse_cap_list(payload)
        elif sub == "DEL":
            payload = rest[-1] if rest else ""
            for name in payload.split():
                self.acked.discard(name)
                self.available.pop(name, None)
        return True

    def _parse_cap_list(self, payload: str) -> None:
        for item in payload.split():
            if "=" in item:
                name, value = item.split("=", 1)
            else:
                name, value = item, None
            self.available[name] = value

    async def _request_desired(self) -> None:
        want = [c for c in DESIRED_CAPS if c in self.available]
        if not want:
            await self._end()
            return
        self._req_sent = True
        await self.client.send("CAP", "REQ", " ".join(want))

    async def _maybe_finish(self) -> None:
        if self._finished or self._waiting_sasl:
            return
        requested = [c for c in DESIRED_CAPS if c in self.available]
        pending = [c for c in requested if c not in self.acked and c not in self.nakked]
        if pending:
            return
        if "sasl" in self.acked and self.client.sasl_enabled:
            self._waiting_sasl = True
            # Non-blocking: start SASL; CAP END happens in on_sasl_done
            await self.client.begin_sasl()
            return
        await self._end()

    async def on_sasl_done(self, success: bool) -> None:
        if not self._waiting_sasl:
            return
        self._waiting_sasl = False
        if not success:
            log.error("SASL failed; ending CAP anyway")
        await self._end()

    async def _end(self) -> None:
        if self._finished:
            return
        await self.client.send("CAP", "END")
        self._finished = True
        log.info("CAP negotiation finished; ack=%s", sorted(self.acked))
