"""HttpServer only binds a socket once a module actually mounts a route."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

from aiohttp import web

from pybot.core.api import BotAPI
from pybot.core.bot import Bot


def _make_bot(tmp_path: Path) -> Bot:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        dedent(
            """\
            irc:
              nick: x
              channels:
                - '#core'
            http:
              host: 127.0.0.1
              port: 0
            modules: {}
            """
        ),
        encoding="utf-8",
    )
    return Bot(cfg)


async def _handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def test_bot_start_never_binds_http_with_no_modules_mounting_routes(
    tmp_path: Path,
) -> None:
    bot = _make_bot(tmp_path)
    bot.irc.connect = AsyncMock()  # type: ignore[method-assign]
    bot.irc.disconnect = AsyncMock()  # type: ignore[method-assign]

    import asyncio

    task = asyncio.create_task(bot.start())
    await asyncio.sleep(0)
    try:
        assert bot.http._started is False
    finally:
        await bot.stop("test")
        await asyncio.wait_for(task, timeout=1)

    # stop() on a server that was never started must not raise or attempt a
    # runner cleanup that was never created.
    assert bot.http._started is False


async def test_mount_route_starts_the_server_on_first_mount(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    api = BotAPI(bot, "test-module")

    assert bot.http._started is False
    await api.mount_route("GET", "/x", _handler)
    try:
        assert bot.http._started is True
    finally:
        await bot.http.stop()


async def test_a_second_mount_does_not_restart_or_rebind(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    api = BotAPI(bot, "test-module")

    await api.mount_route("GET", "/x", _handler)
    runner_after_first = bot.http._runner
    try:
        await api.mount_route("GET", "/y", _handler)
        assert bot.http._runner is runner_after_first
    finally:
        await bot.http.stop()
