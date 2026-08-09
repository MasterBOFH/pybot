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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return _deep_merge(DEFAULTS, data)


def module_config(config: dict[str, Any], name: str) -> dict[str, Any]:
    modules = config.get("modules") or {}
    cfg = modules.get(name) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}
