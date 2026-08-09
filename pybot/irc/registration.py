"""NICK/USER/PASS registration helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pybot.irc.client import IRCClient

log = logging.getLogger("pybot.irc.registration")


async def send_registration(
    client: IRCClient,
    *,
    nick: str,
    username: str,
    realname: str,
    password: str | None = None,
) -> None:
    if password:
        await client.send("PASS", password)
    await client.send("NICK", nick)
    await client.send("USER", username, "0", "*", realname)
    log.info("Sent registration for nick=%s user=%s", nick, username)
