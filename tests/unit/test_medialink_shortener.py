from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from pybot.modules.medialink.module import MedialinkModule


def _module() -> MedialinkModule:
    mod = MedialinkModule()
    mod.api = SimpleNamespace(log=Mock())
    mod.config = {"token_ttl_minutes": 60}
    return mod


def test_local_shortener_creates_mapping_and_url() -> None:
    mod = _module()
    mod._shortener_mode = "local"
    mod._shortener_local_base = "https://sho.rt"
    mod._shortener_local_path = "/j"

    short = mod._shorten_local("https://frontend.example/?token=abc")

    assert short.startswith("https://sho.rt/j?c=")
    code = short.split("c=", 1)[1]
    assert code in mod._shortlinks
    assert mod._shortlinks[code]["url"] == "https://frontend.example/?token=abc"
    assert int(mod._shortlinks[code]["expires_at"]) > int(time.time())


def test_cleanup_shortlinks_removes_expired_entries() -> None:
    mod = _module()
    now = int(time.time())
    mod._shortlinks = {
        "alive": {"url": "https://a", "expires_at": now + 60},
        "dead": {"url": "https://b", "expires_at": now - 1},
    }

    mod._cleanup_shortlinks()

    assert "alive" in mod._shortlinks
    assert "dead" not in mod._shortlinks


def test_local_shortlink_handler_redirects() -> None:
    mod = _module()
    mod._shortlinks = {
        "abc": {"url": "https://frontend.example/?token=abc", "expires_at": int(time.time()) + 60}
    }
    req = SimpleNamespace(query={"c": "abc"})

    with pytest.raises(web.HTTPFound) as exc:
        asyncio.run(mod._shortlink_handler(req))

    assert exc.value.location == "https://frontend.example/?token=abc"


def test_shorten_join_url_tinyurl_mode_uses_backend() -> None:
    mod = _module()
    mod._shortener_mode = "tinyurl"
    mod._shorten_tinyurl = AsyncMock(return_value="https://tinyurl.com/x")

    out = asyncio.run(mod._shorten_join_url("https://frontend.example/?token=abc"))

    assert out == "https://tinyurl.com/x"
    mod._shorten_tinyurl.assert_awaited_once()


def test_shorten_join_url_isgd_mode_uses_backend() -> None:
    mod = _module()
    mod._shortener_mode = "isgd"
    mod._shorten_isgd = AsyncMock(return_value="https://is.gd/x")

    out = asyncio.run(mod._shorten_join_url("https://frontend.example/?token=abc"))

    assert out == "https://is.gd/x"
    mod._shorten_isgd.assert_awaited_once()
