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
async def test_whox_parsing_does_not_mark_oper_without_star_in_flags() -> None:
    c = _client()
    # No '*' in the WHOX flags field means non-oper.
    msg = parse_message(
        ":irc.example 354 pybot 0 #chan ident host.example Alice account H :Alice Example"
    )

    await c._handle_who_354(msg)

    user = c.state.get_user("Alice")
    assert user is not None
    assert user.oper is False


@pytest.mark.asyncio
async def test_whox_parsing_marks_oper_when_star_present_in_flags() -> None:
    c = _client()
    # WHOX tcuhnaor: flags (o) before trailing realname (r).
    msg = parse_message(
        ":irc.example 354 pybot 0 #chan ident host.example Alice account H* :Alice Example"
    )

    await c._handle_who_354(msg)

    user = c.state.get_user("Alice")
    assert user is not None
    assert user.oper is True
    assert user.realname == "Alice Example"


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
