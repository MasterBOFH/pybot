"""Unit tests for WHO/WHOX parsing behavior."""

from __future__ import annotations

import pytest

from pybot.irc.client import IRCClient
from pybot.irc.protocol import parse_message


def _client() -> IRCClient:
    return IRCClient(
        {
            "host": "example.invalid",
            "port": 6667,
            "nick": "pybot",
            "flood": {"burst": 5, "rate": 1.0},
        }
    )


@pytest.mark.asyncio
async def test_whox_parsing_uses_trailing_realname_and_flags_field() -> None:
    c = _client()
    # WHOX requests channel, user, host, nick, flags, account, realname.
    msg = parse_message(
        ":irc.example 354 pybot #chan ident host.example Alice H account :Alice Example"
    )

    await c._handle_who_354(msg)

    user = c.state.get_user("Alice")
    assert user is not None
    assert user.oper is False


@pytest.mark.asyncio
async def test_whox_parsing_keeps_account_and_realname() -> None:
    c = _client()
    # Account and realname should both be preserved when WHOX requests them.
    msg = parse_message(
        ":irc.example 354 pybot #chan ident host.example Alice H* account :Alice Example"
    )

    await c._handle_who_354(msg)

    user = c.state.get_user("Alice")
    assert user is not None
    assert user.realname == "Alice Example"
    assert user.account == "account"
    assert user.oper is True


@pytest.mark.asyncio
async def test_who_352_strips_leading_hopcount_from_realname() -> None:
    c = _client()
    msg = parse_message(
        ":irc.example 352 pybot #chan ident host.example server Alice H :42 Alice Example"
    )

    await c._handle_who_352(msg)

    user = c.state.get_user("Alice")
    assert user is not None
    assert user.realname == "Alice Example"
