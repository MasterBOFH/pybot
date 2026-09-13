"""Third-party loggers (gardena SDK, websockets) are routed through pybot's
handlers, and the SDK's logging.basicConfig() call is neutralised."""

from __future__ import annotations

import logging

import pytest

from pybot.logging_setup import ForwardHandler, adopt_logger, setup_logging
from pybot.modules.gardena.api import _SDKTokenRedactor


class _Capture(logging.Handler):
    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def pybot_capture():
    """setup_logging() then swap the pybot stderr handler for a capture."""
    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)
    saved_foreign = {}
    for name in ("gardena.smart_system", "websockets", "third.party"):
        lg = logging.getLogger(name)
        saved_foreign[name] = (list(lg.handlers), lg.propagate, lg.level)

    setup_logging({"level": "INFO", "raw_enabled": False})
    pybot = logging.getLogger("pybot")
    cap = _Capture(level=pybot.handlers[0].level)
    pybot.handlers[:] = [cap]
    try:
        yield cap
    finally:
        root.handlers[:], root.level = saved_root[0], saved_root[1]
        for name, (handlers, propagate, level) in saved_foreign.items():
            lg = logging.getLogger(name)
            lg.handlers[:] = handlers
            lg.propagate = propagate
            lg.setLevel(level)
        pybot.handlers.clear()


def _set_pybot_level(cap: _Capture, level: int) -> None:
    logging.getLogger("pybot").setLevel(level)
    cap.setLevel(level)


def test_setup_logging_neutralises_basic_config(pybot_capture):
    root = logging.getLogger()
    before = list(root.handlers)
    assert any(isinstance(h, ForwardHandler) for h in before)

    # What py-smart-gardena's SmartSystem.__init__ does.
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    assert root.handlers == before
    assert root.level == logging.WARNING


def test_setup_logging_is_idempotent_on_root(pybot_capture):
    setup_logging({"level": "INFO", "raw_enabled": False})
    setup_logging({"level": "INFO", "raw_enabled": False})
    forwards = [h for h in logging.getLogger().handlers if isinstance(h, ForwardHandler)]
    assert len(forwards) == 1


def test_adopted_logger_is_renamed_and_forwarded(pybot_capture):
    _set_pybot_level(pybot_capture, logging.DEBUG)
    adopt_logger("websockets", into="pybot.modules.gardena.ws")

    logging.getLogger("websockets.client").debug("%% sent keepalive %s", "ping")

    assert [r.name for r in pybot_capture.records] == ["pybot.modules.gardena.ws"]
    assert pybot_capture.records[0].getMessage() == "% sent keepalive ping"


def test_adopted_debug_is_gated_by_pybot_level(pybot_capture):
    _set_pybot_level(pybot_capture, logging.INFO)
    adopt_logger("gardena.smart_system", into="pybot.modules.gardena.sdk")

    sdk = logging.getLogger("gardena.smart_system")
    sdk.debug("Trying to get Websocket url")
    sdk.info("connected")

    assert [r.getMessage() for r in pybot_capture.records] == ["connected"]


def test_adopt_logger_is_idempotent(pybot_capture):
    _set_pybot_level(pybot_capture, logging.DEBUG)
    adopt_logger("websockets", into="pybot.modules.gardena.ws")
    adopt_logger("websockets", into="pybot.modules.gardena.ws")

    logging.getLogger("websockets").debug("once")

    assert len(pybot_capture.records) == 1
    assert len(logging.getLogger("websockets").handlers) == 1


def test_unadopted_third_party_warning_uses_pybot_handlers(pybot_capture):
    logging.getLogger("third.party").warning("careful")
    logging.getLogger("third.party").info("quiet")

    assert [(r.name, r.getMessage()) for r in pybot_capture.records] == [
        ("third.party", "careful")
    ]


def test_sdk_token_is_redacted(pybot_capture):
    _set_pybot_level(pybot_capture, logging.DEBUG)
    adopt_logger(
        "gardena.smart_system",
        into="pybot.modules.gardena.sdk",
        filters=(_SDKTokenRedactor(),),
    )
    token = "eyJhbGciOiJSUzI1NiJ9.secret.sig"
    # TokenManager uses an f-string, so the token is baked into msg.
    logging.getLogger("gardena.smart_system").debug(f"We got a token : {token}")

    msg = pybot_capture.records[0].getMessage()
    assert token not in msg
    assert msg == "We got a token : <redacted>"
