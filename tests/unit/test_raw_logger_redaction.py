"""RawLogger must never write an OPER/PASS password into the raw log.

config.yaml.example ships with raw_enabled: true / raw_level: DEBUG and its own
stderr handler, so every outbound line reaches logs/journald/docker logs. An
unmasked ``OPER <name> <password>`` (hubstats, on every connect) or ``PASS
<serverpassword>`` (registration) would be cleartext credentials at rest.
"""

from __future__ import annotations

import logging

from pybot.logging_setup import RawLogger


def _capture(caplog, line: str) -> str:
    logger = logging.getLogger("pybot.raw.test")
    logger.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="pybot.raw.test")
    caplog.clear()
    RawLogger(logger).outbound(line)
    return caplog.records[-1].getMessage()


def test_outbound_oper_password_is_redacted(caplog) -> None:
    message = _capture(caplog, "OPER myname mypassword")

    assert "mypassword" not in message
    assert "myname" not in message
    assert message == ">> OPER <redacted>"


def test_outbound_pass_password_is_redacted(caplog) -> None:
    message = _capture(caplog, "PASS serverpass")

    assert "serverpass" not in message
    assert message == ">> PASS <redacted>"


def test_outbound_lowercase_sensitive_command_is_still_redacted(caplog) -> None:
    """format_line uppercases, but masking must not depend on that."""
    message = _capture(caplog, "oper myname mypassword")

    assert "mypassword" not in message
    assert message == ">> OPER <redacted>"


def test_outbound_ordinary_line_is_logged_verbatim(caplog) -> None:
    assert _capture(caplog, "PRIVMSG #chan :hello") == ">> PRIVMSG #chan :hello"


def test_outbound_command_only_lines_are_unaffected(caplog) -> None:
    """A bare command with no params must not trip the parser."""
    assert _capture(caplog, "PING") == ">> PING"
    assert _capture(caplog, "") == ">> "


def test_outbound_passive_lookalike_command_is_not_masked(caplog) -> None:
    """Only exact OPER/PASS, not every command starting with those letters."""
    assert _capture(caplog, "PASSWORDLESS foo") == ">> PASSWORDLESS foo"
    assert _capture(caplog, "OPERWALL :hi") == ">> OPERWALL :hi"


def test_inbound_is_untouched(caplog) -> None:
    logger = logging.getLogger("pybot.raw.test")
    logger.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="pybot.raw.test")
    caplog.clear()

    RawLogger(logger).inbound(":irc.example.net 381 pybot :You are now an IRC operator")

    assert caplog.records[-1].getMessage() == (
        "<< :irc.example.net 381 pybot :You are now an IRC operator"
    )
