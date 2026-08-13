"""Unit tests for oidentd user config writer."""

from __future__ import annotations

from pathlib import Path

from pybot.core.oidentd import render_user_config, write_oidentd_user_config


def test_render_user_config_global() -> None:
    content = render_user_config(reply="pybot")
    assert "global {" in content
    assert 'reply "pybot"' in content


def test_render_user_config_server_scoped() -> None:
    content = render_user_config(reply="pybot", server_host="irc.example.net", server_port=6697)
    assert "to irc.example.net fport 6697 {" in content


def test_write_oidentd_user_config_uses_username_default(tmp_path: Path) -> None:
    out = tmp_path / "oidentd.conf"
    path = write_oidentd_user_config(
        {
            "username": "pybot",
            "oidentd": {
                "enabled": True,
                "path": str(out),
            },
        }
    )
    assert path == out
    assert out.exists()
    assert 'reply "pybot"' in out.read_text(encoding="utf-8")


def test_write_oidentd_user_config_skips_invalid_port(tmp_path: Path) -> None:
    out = tmp_path / "oidentd.conf"
    path = write_oidentd_user_config(
        {
            "username": "pybot",
            "oidentd": {
                "enabled": True,
                "path": str(out),
                "server_port": "not-a-number",
            },
        }
    )
    assert path is None
    assert not out.exists()
