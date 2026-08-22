"""Auto-reconnect failures are logged quietly -- they're expected/handled,
not crashes, so no traceback spam on a persistently broken connection."""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

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
            modules: {}
            """
        ),
        encoding="utf-8",
    )
    bot = Bot(cfg)
    bot.http.start = AsyncMock()  # type: ignore[method-assign]
    bot.http.stop = AsyncMock()  # type: ignore[method-assign]
    return bot


async def test_auto_reconnect_failure_logs_a_warning_not_a_traceback(
    tmp_path: Path, caplog
) -> None:
    bot = _make_bot(tmp_path)
    bot._reconnect_now = AsyncMock(side_effect=ConnectionError("wrong version number"))  # type: ignore[method-assign]

    # setup_logging() (called by Bot.__init__) sets the "pybot" logger's
    # propagate=False and routes it through its own handler instead -- flip
    # it back on so caplog's root-attached handler actually sees the record.
    pybot_logger = logging.getLogger("pybot")
    original_propagate = pybot_logger.propagate
    pybot_logger.propagate = True
    try:
        with caplog.at_level(logging.DEBUG, logger="pybot.core.bot"):
            await bot._auto_reconnect_attempt()
    finally:
        pybot_logger.propagate = original_propagate

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("wrong version number" in r.getMessage() for r in warnings)
    assert not any(r.levelno == logging.ERROR for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


async def test_auto_reconnect_failure_still_reschedules(tmp_path: Path) -> None:
    bot = _make_bot(tmp_path)
    bot._reconnect_now = AsyncMock(side_effect=ConnectionError("boom"))  # type: ignore[method-assign]

    assert bot._reconnect_timer is None
    await bot._auto_reconnect_attempt()
    assert bot._reconnect_timer is not None

    bot.timers.cancel_all()
