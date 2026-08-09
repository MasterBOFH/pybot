"""Admin hostmask matching."""

from __future__ import annotations

from pybot.core.bot import Bot
from pybot.irc.isupport import ISupport


class _FakeIRC:
    def __init__(self) -> None:
        self.isupport = ISupport()
        self.state = type("S", (), {"get_user": lambda self, n: None})()


def test_hostmask_match_variants(tmp_path) -> None:
    # Minimal Bot without connecting — only need _hostmask_match
    cfg = tmp_path / "c.yaml"
    cfg.write_text("irc: {nick: x}\n", encoding="utf-8")
    bot = Bot(cfg)
    bot.irc = _FakeIRC()  # type: ignore[assignment]

    assert bot._hostmask_match("*!dan@*.example.net", "Alice", "dan", "a.example.net")
    assert bot._hostmask_match("dan@*.example.net", "Bob", "dan", "x.example.net")
    assert bot._hostmask_match("vmi.example.net", "Bob", "u", "vmi.example.net")
    assert not bot._hostmask_match("*!dan@*.example.net", "Alice", "other", "a.example.net")
    # rfc1459 nick fold in pattern
    assert bot._hostmask_match("al[]ce!*@*", "AL{}CE", "u", "h")
