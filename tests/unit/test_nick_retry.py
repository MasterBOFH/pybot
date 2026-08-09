"""Unit tests for nick collision fallbacks and dash nicks."""

from __future__ import annotations

from pybot.irc.client import dashed_nick, fit_nick
from pybot.irc.isupport import ISupport


def test_fit_and_dashed_nick() -> None:
    assert fit_nick("GoBNC", 12) == "GoBNC"
    assert dashed_nick("GoBNC", 1, nicklen=12) == "GoBNC-"
    assert dashed_nick("GoBNC", 2, nicklen=12) == "GoBNC--"


def test_dashed_nick_respects_nicklen() -> None:
    nick = "ABCDEFGHIJKL"
    assert len(nick) == 12
    alt = dashed_nick(nick, 1, nicklen=12)
    assert len(alt) == 12
    assert alt.endswith("-")
    assert alt != nick


def test_isupport_nicklen() -> None:
    i = ISupport()
    assert i.nicklen == 30
    i.parse_tokens(["NICKLEN=12"])
    assert i.nicklen == 12


def test_registration_fallback_order() -> None:
    """primary → altnick → primary- → primary--"""
    from pybot.irc.client import IRCClient

    c = IRCClient(
        {
            "host": "x",
            "port": 6667,
            "nick": "GoBNC",
            "altnick": "GoBNC2",
            "flood": {"burst": 5, "rate": 10},
        }
    )
    c.isupport.nicklen = 12
    assert c._next_registration_nick("GoBNC") == "GoBNC2"
    assert c._alt_tried
    assert c._next_registration_nick("GoBNC2") == "GoBNC-"
    assert c._next_registration_nick("GoBNC-") == "GoBNC--"


def test_registration_fallback_without_altnick() -> None:
    from pybot.irc.client import IRCClient

    c = IRCClient(
        {
            "host": "x",
            "port": 6667,
            "nick": "GoBNC",
            "flood": {"burst": 5, "rate": 10},
        }
    )
    c.isupport.nicklen = 12
    assert c._next_registration_nick("GoBNC") == "GoBNC-"
