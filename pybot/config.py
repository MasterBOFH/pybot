"""YAML configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "irc": {
        "host": "localhost",
        "port": 6667,
        "tls": False,
        "tls_verify": True,
        "bindhost": None,  # local IP/hostname to bind outbound IRC socket
        "nick": "pybot",
        "altnick": None,
        "username": "pybot",
        "realname": "pybot",
        "password": None,
        "oidentd": {
            "enabled": False,
            "path": "~/.config/oidentd.conf",
            "reply": None,
            "server_host": None,
            "server_port": None,
        },
        "ison_poll_seconds": 30,
        "sasl": {
            "enabled": False,
            "mechanism": "auto",
            "username": "",
            "password": "",
        },
        "channels": [],
        "who_poll_seconds": 60,
        "reconnect": {
            "enabled": True,
            "initial_delay": 10,
            "step": 10,
            "max_delay": 60,
        },
        "flood": {
            "burst": 5,
            "rate": 1.0,
        },
        "admin": {
            "hosts": [],       # hostmasks: nick!user@host with * ? wildcards
            "accounts": [],    # services account names
            "prefix": "~",
        },
    },
    "http": {
        "host": "0.0.0.0",
        "port": 8080,
    },
    "logging": {
        "level": "INFO",
        "raw_enabled": True,
        "raw_level": "DEBUG",
        "color": True,
    },
    "modules": {},
}


class ConfigError(ValueError):
    """Raised when the config file cannot be loaded or parsed."""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _format_yaml_error(path: Path, exc: yaml.YAMLError) -> str:
    parts = [f"Invalid YAML in {path}"]
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        parts[0] += f" at line {mark.line + 1}, column {mark.column + 1}"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        if lines:
            start = max(0, mark.line - 1)
            end = min(len(lines), mark.line + 2)
            width = len(str(end))
            for line_no in range(start, end):
                prefix = ">" if line_no == mark.line else " "
                parts.append(f"{prefix} {line_no + 1:>{width}} | {lines[line_no]}")
            parts.append(f"  {' ' * width} | {' ' * mark.column}^")

    details = [text for text in (getattr(exc, "context", None), getattr(exc, "problem", None)) if text]
    if not details:
        details = [str(exc)]
    parts.extend(details)
    return "\n".join(parts)


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(_format_yaml_error(path, exc)) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping: {path}")
    return _deep_merge(DEFAULTS, data)


def module_config(config: dict[str, Any], name: str) -> dict[str, Any]:
    modules = config.get("modules") or {}
    cfg = modules.get(name) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}
