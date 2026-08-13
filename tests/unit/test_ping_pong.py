"""Unit tests for IRCClient PING/PONG behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pybot.irc.client import IRCClient
from pybot.irc.protocol import parse_message


def _mk_client() -> IRCClient:
    c = IRCClient(
        {
            "host": "example.com",
            "port": 6667,
            "nick": "pybot",
            "flood": {"burst": 5, "rate": 1.0},
        }
    )
    c.caps.handle = AsyncMock(return_value=False)
    c.conn.send_raw = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_ping_trailing_payload_keeps_colon_in_pong() -> None:
    c = _mk_client()
    msg = parse_message("PING :1066872259")

    await c._dispatch(msg)

    c.conn.send_raw.assert_awaited_once_with("PONG :1066872259")


@pytest.mark.asyncio
async def test_ping_middle_payload_uses_standard_pong() -> None:
    c = _mk_client()
    msg = parse_message("PING 1066872259")

    await c._dispatch(msg)

    c.conn.send_raw.assert_awaited_once_with("PONG :1066872259")


@pytest.mark.asyncio
async def test_ping_two_params_keeps_raw_shape() -> None:
    c = _mk_client()
    msg = parse_message("PING irc.example.net :1066872259")

    await c._dispatch(msg)

    c.conn.send_raw.assert_awaited_once_with("PONG :1066872259")
