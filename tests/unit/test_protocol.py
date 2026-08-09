"""Unit tests for IRC line parsing (no server required)."""

from __future__ import annotations

from pybot.irc.protocol import format_line, parse_message


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
