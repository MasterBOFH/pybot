"""Exit-code plumbing: request_exit_code() → stop() → start()'s return value."""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

import pytest

from pybot.core.bot import Bot


def _make_bot(tmp_path: Path) -> Bot:
    """A Bot that can run start()/stop() without touching the network."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        dedent(
            """\
            irc:
              nick: x
              channels:
                - '#core'
            modules: {}
            """
        ),
        encoding="utf-8",
    )
    bot = Bot(cfg)
    bot.http.start = AsyncMock()  # type: ignore[method-assign]
    bot.http.stop = AsyncMock()  # type: ignore[method-assign]
    bot.irc.connect = AsyncMock()  # type: ignore[method-assign]
    bot.irc.disconnect = AsyncMock()  # type: ignore[method-assign]
    return bot


async def test_start_returns_zero_without_request_exit_code(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)

    task = asyncio.create_task(bot.start())
    await asyncio.sleep(0)
    await bot.stop("test")

    assert await asyncio.wait_for(task, timeout=1) == 0


async def test_start_returns_requested_exit_code(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)

    task = asyncio.create_task(bot.start())
    await asyncio.sleep(0)
    bot.request_exit_code(1)
    await bot.stop("some reason")

    assert await asyncio.wait_for(task, timeout=1) == 1


async def test_last_requested_exit_code_wins(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)

    task = asyncio.create_task(bot.start())
    await asyncio.sleep(0)
    bot.request_exit_code(1)
    bot.request_exit_code(3)
    await bot.stop("some reason")

    assert await asyncio.wait_for(task, timeout=1) == 3


async def test_request_exit_code_alone_does_not_stop_the_bot(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)

    task = asyncio.create_task(bot.start())
    await asyncio.sleep(0)
    bot.request_exit_code(2)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.05)

    assert not bot._stop_event.is_set()
    task.cancel()


def test_main_returns_the_code_from_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pybot import __main__ as entrypoint

    cfg = tmp_path / "c.yaml"
    cfg.write_text("irc: {nick: x}\nmodules: {}\n", encoding="utf-8")

    class _FakeBot:
        def __init__(self, _path: object) -> None:
            pass

        async def start(self) -> int:
            return 7

    monkeypatch.setattr(entrypoint, "Bot", _FakeBot)

    assert entrypoint.main([str(cfg)]) == 7


def test_main_returns_zero_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pybot import __main__ as entrypoint

    cfg = tmp_path / "c.yaml"
    cfg.write_text("irc: {nick: x}\nmodules: {}\n", encoding="utf-8")

    class _FakeBot:
        def __init__(self, _path: object) -> None:
            pass

        async def start(self) -> int:
            raise KeyboardInterrupt

    monkeypatch.setattr(entrypoint, "Bot", _FakeBot)

    assert entrypoint.main([str(cfg)]) == 0
