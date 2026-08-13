"""Admin hostmask matching."""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

from pybot.core.bot import Bot
from pybot.irc.isupport import ISupport
from pybot.irc.protocol import parse_message


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


def test_configured_join_channels_are_core_only_until_a_module_registers(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        dedent(
            """\
            irc:
              nick: x
              channels:
                - '#core'
            modules:
              github:
                enabled: true
                channel: '#dev'
              gardena:
                enabled: true
                channels:
                  - '#ops'
                  - '#alerts'
            """
        ),
        encoding="utf-8",
    )
    bot = Bot(cfg)

    assert bot._configured_join_channels() == [("#core", None)]
    bot.register_wanted_channels("module:github", ["#dev", "#ops", "#alerts"])

    assert bot._configured_join_channels() == [
        ("#core", None),
        ("#dev", None),
        ("#ops", None),
        ("#alerts", None),
    ]
    assert bot.irc._channels_to_join == bot._configured_join_channels()  # type: ignore[attr-defined]


def test_module_setup_registers_repo_channels_with_bot_api(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        dedent(
            """\
            irc:
              nick: x
              channels:
                - '#core'
            modules:
              github:
                enabled: true
                channel: '#dev'
                repos:
                  - name: 'org/repo1'
                    channels:
                      - '#ops'
                      - '#github'
                  - name: 'org/repo2'
                    channel: '#release'
            """
        ),
        encoding="utf-8",
    )
    bot = Bot(cfg)
    mod = __import__("pybot.modules.github.module", fromlist=["GitHubModule"]).GitHubModule()
    mod.config = {
        "channel": "#dev",
        "repos": [
            {"name": "org/repo1", "channels": ["#ops", "#github"]},
            {"name": "org/repo2", "channel": "#release"},
        ],
    }

    asyncio.run(mod.setup(__import__("pybot.core.api", fromlist=["BotAPI"]).BotAPI(bot, "github")))

    assert bot._configured_join_channels() == [
        ("#core", None),
        ("#dev", None),
        ("#ops", None),
        ("#github", None),
        ("#release", None),
    ]


def test_invite_to_registered_channel_auto_joins(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        dedent(
            """\
            irc:
              nick: x
              channels:
                - '#core'
            """
        ),
        encoding="utf-8",
    )
    bot = Bot(cfg)
    bot.register_wanted_channels("module:github", ["#dev"])
    bot.irc.registered = True
    bot.irc.conn = type("Conn", (), {"connected": True})()
    bot.irc.join = AsyncMock()

    asyncio.run(bot.irc._dispatch(parse_message(":alice!u@h INVITE x :#dev")))

    bot.irc.join.assert_awaited_once_with("#dev")


def test_admin_commands_only_work_in_core_channels(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """irc:
  nick: x
  channels:
    - '#core'
  admin:
    hosts:
      - '*!admin@host'
    prefix: '~'
modules: {}
""",
        encoding="utf-8",
    )
    bot = Bot(cfg)
    bot.irc.privmsg = AsyncMock()  # type: ignore[assignment]
    bot.reload_modules = AsyncMock()  # type: ignore[assignment]

    allowed = {
        "nick": "Alice",
        "user": "admin",
        "host": "host",
        "target": "#core",
        "text": "~reload modules",
    }
    blocked = dict(allowed, target="#module")

    asyncio.run(bot._maybe_admin(allowed))
    asyncio.run(bot._maybe_admin(blocked))

    assert bot.reload_modules.await_count == 1
    bot.irc.privmsg.assert_awaited_once_with("#core", "reloaded modules")


def test_admin_commands_work_in_private_message(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """irc:
  nick: pybot
  channels:
    - '#core'
  admin:
    hosts:
      - '*!admin@host'
    prefix: '~'
modules: {}
""",
        encoding="utf-8",
    )
    bot = Bot(cfg)
    bot.irc.privmsg = AsyncMock()  # type: ignore[assignment]
    bot.reload_modules = AsyncMock()  # type: ignore[assignment]

    private_message = {
        "nick": "Alice",
        "user": "admin",
        "host": "host",
        "target": "pybot",
        "text": "~reload modules",
    }

    asyncio.run(bot._maybe_admin(private_message))

    bot.reload_modules.assert_awaited_once()
    bot.irc.privmsg.assert_awaited_once_with("Alice", "reloaded modules")


def test_connection_close_closes_writer_before_cancelling_reader() -> None:
    from pybot.irc.connection import Connection

    conn = Connection("127.0.0.1", 6667)

    events: list[str] = []

    class FakeReader:
        pass

    class FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True
            events.append("cancel")

        def done(self) -> bool:
            return self.cancelled

        def __await__(self):
            async def _wait() -> None:
                return None

            return _wait().__await__()

    class FakeWriter:
        def close(self) -> None:
            events.append("writer.close")

        async def wait_closed(self) -> None:
            return None

    conn._reader = FakeReader()
    conn._writer = FakeWriter()
    conn._read_task = FakeTask()
    conn.connected = True

    asyncio.run(conn.close())

    assert events == ["writer.close", "cancel"]
