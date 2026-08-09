"""Console logging with severity levels and a separate raw IRC logger."""

from __future__ import annotations

import logging
import sys
from typing import Any


_LEVEL_NAMES = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

# Fixed width so INFO / WARNING / DEBUG columns align
_LEVEL_WIDTH = 8

# ANSI
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_COLORS = {
    logging.DEBUG: "\033[36m",      # cyan
    logging.INFO: "\033[32m",       # green
    logging.WARNING: "\033[33m",    # yellow
    logging.ERROR: "\033[31m",      # red
    logging.CRITICAL: "\033[35m",   # magenta
}
_NAME_COLOR = "\033[34m"            # blue
_RAW_LEVEL = "\033[90m"             # bright black / grey (level column)
_RAW_IN = "\033[36m"                # cyan  <<
_RAW_OUT = "\033[35m"               # magenta >>


def _short_name(name: str) -> str:
    """pybot.irc.client → irc.client; pybot.modules.x → modules.x"""
    if name == "pybot":
        return "pybot"
    if name.startswith("pybot."):
        return name[len("pybot.") :]
    return name


class SeverityFormatter(logging.Formatter):
    def __init__(self, use_color: bool = True, name_width: int = 22) -> None:
        super().__init__()
        self.use_color = use_color and sys.stderr.isatty()
        self.name_width = name_width

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level_plain = record.levelname.ljust(_LEVEL_WIDTH)
        name_plain = _short_name(record.name).ljust(self.name_width)
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"

        if self.use_color:
            color = _COLORS.get(record.levelno, "")
            ts_s = f"{_DIM}{ts}{_RESET}"
            level_s = f"{_BOLD}{color}{level_plain}{_RESET}"
            name_s = f"{_NAME_COLOR}{name_plain}{_RESET}"
            # Tint message slightly by severity for warnings+
            if record.levelno >= logging.WARNING:
                msg_s = f"{color}{msg}{_RESET}"
            else:
                msg_s = msg
            return f"{ts_s}  {level_s}  {name_s}  {msg_s}"

        return f"{ts}  {level_plain}  {name_plain}  {msg}"


class RawFormatter(logging.Formatter):
    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        msg = record.getMessage()
        level_plain = "RAW".ljust(_LEVEL_WIDTH)
        if self.use_color:
            if msg.startswith("<<"):
                msg = f"{_RAW_IN}{msg}{_RESET}"
            elif msg.startswith(">>"):
                msg = f"{_RAW_OUT}{msg}{_RESET}"
            ts_s = f"{_DIM}{ts}{_RESET}"
            level_s = f"{_BOLD}{_RAW_LEVEL}{level_plain}{_RESET}"
            return f"{ts_s}  {level_s}  {msg}"
        return f"{ts}  {level_plain}  {msg}"


class RawLogger:
    """Dedicated logger for raw IRC lines (<< inbound, >> outbound)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("pybot.raw")

    @property
    def enabled(self) -> bool:
        return self._log.isEnabledFor(logging.DEBUG) or self._log.isEnabledFor(
            logging.INFO
        )

    def inbound(self, line: str) -> None:
        self._log.debug("<< %s", line)

    def outbound(self, line: str) -> None:
        self._log.debug(">> %s", line)

    def set_level(self, level: int | str) -> None:
        if isinstance(level, str):
            level = _LEVEL_NAMES.get(level.upper(), logging.DEBUG)
        self._log.setLevel(level)


def _parse_level(value: str | int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return _LEVEL_NAMES.get(str(value).upper(), default)


def setup_logging(config: dict[str, Any] | None = None) -> RawLogger:
    """Configure pybot and pybot.raw loggers from config dict."""
    config = config or {}
    level = _parse_level(config.get("level"), logging.INFO)
    raw_enabled = config.get("raw_enabled", True)
    raw_level = _parse_level(config.get("raw_level"), logging.DEBUG)
    use_color = config.get("color", True)

    root = logging.getLogger("pybot")
    root.handlers.clear()
    root.setLevel(level)
    root.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SeverityFormatter(use_color=use_color))
    handler.setLevel(level)
    root.addHandler(handler)

    raw = logging.getLogger("pybot.raw")
    raw.handlers.clear()
    raw.propagate = False
    if raw_enabled:
        raw.setLevel(raw_level)
        rh = logging.StreamHandler(sys.stderr)
        rh.setFormatter(RawFormatter(use_color=use_color))
        rh.setLevel(raw_level)
        raw.addHandler(rh)
    else:
        raw.setLevel(logging.CRITICAL + 1)
        raw.addHandler(logging.NullHandler())

    return RawLogger(raw)


def get_module_logger(name: str) -> logging.Logger:
    """Return a child logger for a module: pybot.modules.<name>."""
    return logging.getLogger(f"pybot.modules.{name}")
