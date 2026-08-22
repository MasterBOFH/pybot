"""Unit tests for raw/kick/oper/stats protocol primitives."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pybot.core.api import BotAPI
from pybot.irc.client import IRCClient


def _mk_client(emit=None) -> IRCClient:
    c = IRCClient(
        {
            "host": "example.com",
            "port": 6667,
            "nick": "pybot",
            "flood": {"burst": 5, "rate": 1.0},
        },
        emit=emit,
    )
    c.caps.handle = AsyncMock(return_value=False)
    c.conn.send_raw = AsyncMock()
    return c


def _mk_api() -> tuple[BotAPI, Any]:
    """BotAPI wired to a fake Bot whose .irc is an AsyncMock."""

    class _Conn:
        connected = True

    class _FakeBot:
        def __init__(self) -> None:
            self.irc = AsyncMock()
            self.irc.conn = _Conn()
            self.request_exit_code = MagicMock()
            self.stop = AsyncMock()

    bot = _FakeBot()
    return BotAPI(bot, "testmod"), bot


# --- IRCClient.oper -------------------------------------------------------


@pytest.mark.asyncio
async def test_oper_success_on_381() -> None:
    c = _mk_client()
    task = asyncio.create_task(c.oper("admin", "secret", timeout=1.0))
    await asyncio.sleep(0)

    c.conn.send_raw.assert_awaited_once_with("OPER admin secret")

    await c._on_line(":irc.example.net 381 pybot :You are now an IRC operator")
    ok, text = await task

    assert ok is True
    assert text == "You are now an IRC operator"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["464", "491"])
async def test_oper_failure_numerics(code: str) -> None:
    c = _mk_client()
    task = asyncio.create_task(c.oper("admin", "wrong", timeout=1.0))
    await asyncio.sleep(0)

    await c._on_line(f":irc.example.net {code} pybot :Nope, denied")
    ok, text = await task

    assert ok is False
    assert text == "Nope, denied"


@pytest.mark.asyncio
async def test_oper_timeout() -> None:
    c = _mk_client()
    ok, text = await c.oper("admin", "secret", timeout=0.05)

    assert ok is False
    assert text == "timed out waiting for OPER reply"


@pytest.mark.asyncio
async def test_oper_ignores_unrelated_numerics() -> None:
    c = _mk_client()
    task = asyncio.create_task(c.oper("admin", "secret", timeout=1.0))
    await asyncio.sleep(0)

    await c._on_line(":irc.example.net 242 pybot :Server up 1 day")
    assert not task.done()

    await c._on_line(":irc.example.net 381 pybot :You are now an IRC operator")
    ok, _text = await task
    assert ok is True


# --- IRCClient.stats ------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_collects_until_219() -> None:
    c = _mk_client()
    task = asyncio.create_task(c.stats("u", timeout=1.0))
    await asyncio.sleep(0)

    c.conn.send_raw.assert_awaited_once_with("STATS u")

    await c._on_line(":irc.example.net 242 pybot :Server Up 5 days")
    await c._on_line(":irc.example.net 243 pybot O * * admin")
    await c._on_line(":irc.example.net 219 pybot u :End of /STATS report")

    lines = await task

    assert lines == [
        (242, ["pybot", "Server Up 5 days"]),
        (243, ["pybot", "O", "*", "*", "admin"]),
    ]


@pytest.mark.asyncio
async def test_stats_returns_partial_on_timeout() -> None:
    c = _mk_client()
    task = asyncio.create_task(c.stats("u", timeout=0.05))
    await asyncio.sleep(0)

    await c._on_line(":irc.example.net 242 pybot :Server Up 5 days")
    lines = await task

    assert lines == [(242, ["pybot", "Server Up 5 days"])]


@pytest.mark.asyncio
async def test_stats_returns_empty_list_on_timeout_with_no_replies() -> None:
    c = _mk_client()
    assert await c.stats("u", timeout=0.05) == []


@pytest.mark.asyncio
async def test_stats_does_not_swallow_numeric_event() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, payload: dict[str, Any]) -> None:
        seen.append((event, payload))

    c = _mk_client(emit=_emit)
    task = asyncio.create_task(c.stats("u", timeout=1.0))
    await asyncio.sleep(0)

    await c._on_line(":irc.example.net 242 pybot :Server Up 5 days")
    await c._on_line(":irc.example.net 219 pybot u :End of /STATS report")
    await task

    codes = [p["code"] for ev, p in seen if ev == "numeric"]
    assert 242 in codes
    assert 219 in codes


@pytest.mark.asyncio
async def test_oper_does_not_swallow_numeric_event() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, payload: dict[str, Any]) -> None:
        seen.append((event, payload))

    c = _mk_client(emit=_emit)
    task = asyncio.create_task(c.oper("admin", "secret", timeout=1.0))
    await asyncio.sleep(0)

    await c._on_line(":irc.example.net 381 pybot :You are now an IRC operator")
    await task

    codes = [p["code"] for ev, p in seen if ev == "numeric"]
    assert codes == [381]


@pytest.mark.asyncio
async def test_stats_and_oper_do_not_interfere() -> None:
    c = _mk_client()
    stats_task = asyncio.create_task(c.stats("u", timeout=1.0))
    await asyncio.sleep(0)
    oper_task = asyncio.create_task(c.oper("admin", "secret", timeout=1.0))
    await asyncio.sleep(0)

    await c._on_line(":irc.example.net 381 pybot :You are now an IRC operator")
    ok, _text = await oper_task
    assert ok is True
    assert not stats_task.done()

    await c._on_line(":irc.example.net 219 pybot u :End of /STATS report")
    lines = await stats_task
    assert lines == [(381, ["pybot", "You are now an IRC operator"])]


# --- IRCClient.kick -------------------------------------------------------


@pytest.mark.asyncio
async def test_client_kick_without_reason() -> None:
    c = _mk_client()
    await c.kick("#chan", "baddie")
    c.conn.send_raw.assert_awaited_once_with("KICK #chan baddie")


@pytest.mark.asyncio
async def test_client_kick_with_reason() -> None:
    c = _mk_client()
    await c.kick("#chan", "baddie", "go away")
    c.conn.send_raw.assert_awaited_once_with("KICK #chan baddie :go away")


@pytest.mark.asyncio
async def test_client_kick_empty_reason_is_omitted() -> None:
    c = _mk_client()
    await c.kick("#chan", "baddie", "")
    c.conn.send_raw.assert_awaited_once_with("KICK #chan baddie")


# --- BotAPI ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_raw_sends_command_and_params() -> None:
    api, bot = _mk_api()
    await api.raw("STATS", "u", "irc.example.net")
    bot.irc.send.assert_awaited_once_with("STATS", "u", "irc.example.net")


@pytest.mark.asyncio
async def test_api_raw_formats_exact_line_via_client() -> None:
    c = _mk_client()
    c.conn.connected = True
    api, bot = _mk_api()
    bot.irc = c

    await api.raw("PRIVMSG", "#chan", "hello there")

    c.conn.send_raw.assert_awaited_once_with("PRIVMSG #chan :hello there")


@pytest.mark.asyncio
async def test_api_raw_skipped_when_disconnected() -> None:
    api, bot = _mk_api()
    bot.irc.conn.connected = False

    await api.raw("STATS", "u")

    bot.irc.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_kick_passes_reason_through() -> None:
    api, bot = _mk_api()
    await api.kick("#chan", "baddie", "go away")
    bot.irc.kick.assert_awaited_once_with("#chan", "baddie", "go away")


@pytest.mark.asyncio
async def test_api_kick_without_reason() -> None:
    api, bot = _mk_api()
    await api.kick("#chan", "baddie")
    bot.irc.kick.assert_awaited_once_with("#chan", "baddie", None)


@pytest.mark.asyncio
async def test_api_kick_skipped_when_disconnected() -> None:
    api, bot = _mk_api()
    bot.irc.conn.connected = False
    await api.kick("#chan", "baddie")
    bot.irc.kick.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_oper_delegates() -> None:
    api, bot = _mk_api()
    bot.irc.oper.return_value = (True, "yay")

    assert await api.oper("admin", "secret") == (True, "yay")
    bot.irc.oper.assert_awaited_once_with("admin", "secret", timeout=10.0)


@pytest.mark.asyncio
async def test_api_stats_delegates() -> None:
    api, bot = _mk_api()
    bot.irc.stats.return_value = [(242, ["pybot", "up"])]

    assert await api.stats("u", timeout=2.5) == [(242, ["pybot", "up"])]
    bot.irc.stats.assert_awaited_once_with("u", timeout=2.5)


@pytest.mark.asyncio
async def test_api_shutdown_requests_exit_code_then_stops() -> None:
    api, bot = _mk_api()
    await api.shutdown("fatal: no hub", exit_code=3)

    bot.request_exit_code.assert_called_once_with(3)
    bot.stop.assert_awaited_once_with("fatal: no hub")


@pytest.mark.asyncio
async def test_api_shutdown_default_exit_code_is_1() -> None:
    api, bot = _mk_api()
    await api.shutdown("bye")

    bot.request_exit_code.assert_called_once_with(1)
