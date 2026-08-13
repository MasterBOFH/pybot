"""Unit tests for YAML config loading."""

from __future__ import annotations

from pathlib import Path

from pybot.config import ConfigError, load_config


def test_load_config_reports_yaml_location(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """irc:\n  host: example.net\n  channels:\n    - '#dev'\n  admin:\n    prefix: '~'\n    - broken\n""",
        encoding="utf-8",
    )

    try:
        load_config(path)
    except ConfigError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected load_config to raise ConfigError")

    assert f"Invalid YAML in {path}" in message
    assert "line 7, column 5" in message
    assert "> 7 |     - broken" in message
    assert "^" in message
    assert "expected <block end>" in message