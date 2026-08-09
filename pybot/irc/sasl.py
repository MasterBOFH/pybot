"""SASL PLAIN and SCRAM-SHA-256 for IRC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pybot.irc.client import IRCClient
    from pybot.irc.protocol import Message

log = logging.getLogger("pybot.irc.sasl")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


class ScramSha256:
    """Minimal SCRAM-SHA-256 client (RFC 5802 / 7677)."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password.encode("utf-8")
        self.client_nonce = _b64(os.urandom(24)).rstrip("=")
        self.client_first_bare = ""
        self.server_signature = b""
        self.state = "client-first"

    def client_first(self) -> str:
        self.client_first_bare = f"n={self.username},r={self.client_nonce}"
        self.state = "server-first"
        return f"n,,{self.client_first_bare}"

    def client_final(self, server_first: str) -> str:
        attrs = dict(pair.split("=", 1) for pair in server_first.split(",") if "=" in pair)
        nonce = attrs["r"]
        if not nonce.startswith(self.client_nonce):
            raise ValueError("SCRAM server nonce does not start with client nonce")
        salt = _b64d(attrs["s"])
        iterations = int(attrs["i"])

        salted = hashlib.pbkdf2_hmac(
            "sha256", self.password, salt, iterations, dklen=32
        )
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        client_final_without_proof = f"c={_b64(b'n,,')},r={nonce}"
        auth_message = (
            f"{self.client_first_bare},{server_first},{client_final_without_proof}"
        ).encode("utf-8")
        client_sig = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, client_sig))
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        self.server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
        self.state = "server-final"
        return f"{client_final_without_proof},p={_b64(proof)}"

    def verify_server_final(self, server_final: str) -> None:
        attrs = dict(pair.split("=", 1) for pair in server_final.split(",") if "=" in pair)
        if "e" in attrs:
            raise ValueError(f"SCRAM error: {attrs['e']}")
        if _b64d(attrs["v"]) != self.server_signature:
            raise ValueError("SCRAM server signature mismatch")
        self.state = "done"


class SaslAuth:
    def __init__(
        self,
        client: IRCClient,
        *,
        username: str,
        password: str,
        mechanism: str = "auto",
    ) -> None:
        self.client = client
        self.username = username
        self.password = password
        self.mechanism_pref = mechanism.upper() if mechanism else "AUTO"
        self.chosen: str | None = None
        self._scram: ScramSha256 | None = None
        self._done = False
        self._success = False

    @property
    def done(self) -> bool:
        return self._done

    @property
    def success(self) -> bool:
        return self._success

    def _pick_mechanism(self) -> str:
        offered = self.client.caps.sasl_mechs()
        pref = self.mechanism_pref
        if pref != "AUTO":
            return pref
        if "SCRAM-SHA-256" in offered:
            return "SCRAM-SHA-256"
        if "PLAIN" in offered or not offered:
            return "PLAIN"
        return offered[0]

    async def begin(self) -> None:
        """Send AUTHENTICATE <mech>; exchange continues via handle()."""
        self.chosen = self._pick_mechanism()
        log.info("Starting SASL %s as %s", self.chosen, self.username)
        await self.client.send("AUTHENTICATE", self.chosen)

    async def handle(self, msg: Message) -> bool:
        if msg.command == "AUTHENTICATE":
            try:
                await self._on_authenticate(msg.params[0] if msg.params else "")
            except Exception:
                log.exception("SASL AUTHENTICATE handling failed")
                await self._finish(False)
            return True
        if msg.command in {"900", "903", "904", "905", "906", "907", "908"}:
            await self._on_numeric(msg)
            return True
        return False

    async def _on_authenticate(self, payload: str) -> None:
        if self.chosen == "PLAIN":
            if payload == "+":
                raw = f"\0{self.username}\0{self.password}".encode("utf-8")
                await self._send_b64(raw)
            return

        if self.chosen == "SCRAM-SHA-256":
            if payload == "+":
                self._scram = ScramSha256(self.username, self.password)
                await self._send_b64(self._scram.client_first().encode("utf-8"))
                return
            if not self._scram:
                return
            data = _b64d(payload).decode("utf-8")
            if self._scram.state == "server-first":
                final = self._scram.client_final(data)
                await self._send_b64(final.encode("utf-8"))
            elif self._scram.state == "server-final":
                self._scram.verify_server_final(data)

    async def _send_b64(self, data: bytes) -> None:
        encoded = _b64(data)
        if not encoded:
            await self.client.send("AUTHENTICATE", "+")
            return
        chunk = ""
        while encoded:
            chunk, encoded = encoded[:400], encoded[400:]
            await self.client.send("AUTHENTICATE", chunk)
        if len(chunk) == 400:
            await self.client.send("AUTHENTICATE", "+")

    async def _on_numeric(self, msg: Message) -> None:
        code = msg.command
        if code == "903":
            log.info("SASL authentication successful")
            await self._finish(True)
        elif code in {"904", "905", "906"}:
            log.error("SASL failed (%s): %s", code, msg.trailing)
            await self._finish(False)
        elif code == "908":
            log.info("SASL mechanisms: %s", msg.trailing)
        elif code == "900":
            log.debug("SASL logged in: %s", msg.trailing)

    async def _finish(self, success: bool) -> None:
        if self._done:
            return
        self._success = success
        self._done = True
        await self.client.caps.on_sasl_done(success)
