"""Unit tests for IRC line parsing (no server required)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from pybot.irc.connection import Connection
from pybot.irc.protocol import format_line, parse_message
from pybot.modules.medialink.module import MedialinkModule


def test_parse_privmsg_with_tags() -> None:
    m = parse_message("@account=alice;time=2020-01-01 :nick!u@h PRIVMSG #chan :hello world")
    assert m.command == "PRIVMSG"
    assert m.tags["account"] == "alice"
    assert m.source_nick == "nick"
    assert m.prefix is not None
    assert m.prefix.user == "u"
    assert m.prefix.host == "h"
    assert m.params == ["#chan", "hello world"]


def test_parse_server_prefix() -> None:
    m = parse_message(":irc.example.net NOTICE * :Looking up your hostname")
    assert m.prefix is not None
    assert m.prefix.is_server
    assert m.command == "NOTICE"


def test_format_trailing_with_spaces() -> None:
    assert format_line("PRIVMSG", "#chan", "hi there") == "PRIVMSG #chan :hi there"
    assert format_line("QUIT", "gone") == "QUIT gone"


def test_parse_numeric() -> None:
    m = parse_message(":server 001 pybot :Welcome")
    assert m.command == "001"
    assert m.params[0] == "pybot"
    assert m.trailing == "Welcome"


@pytest.mark.asyncio
async def test_connection_close_does_not_wait_forever_on_writer_close() -> None:
    class SlowWriter:
        def __init__(self) -> None:
            self.closed = False
            self.transport = self

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            await asyncio.sleep(60)

    conn = Connection("example.com", 6667)
    conn._writer = SlowWriter()
    conn._read_task = asyncio.create_task(asyncio.sleep(0))

    await asyncio.wait_for(conn.close(), timeout=2.5)

    assert conn._writer is None


@pytest.mark.asyncio
async def test_medialink_announcement_skips_when_irc_is_disconnected() -> None:
    module = MedialinkModule()
    module._channels = [("#chan", False)]
    module.api = type(
        "API",
        (),
        {
            "log": logging.getLogger("test.medialink"),
            "privmsg": AsyncMock(side_effect=RuntimeError("Not connected")),
        },
    )()

    await module._announce("info", "hello")

    module.api.privmsg.assert_awaited_once_with("#chan", "hello")
