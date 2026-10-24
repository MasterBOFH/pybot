from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from pybot.modules.imdb.module import ImdbModule
from pybot.modules.imdb.omdb import (
    OmdbClient,
    OmdbError,
    find_imdb_ids,
    format_title,
    parse_query,
)

SHAWSHANK = {
    "Response": "True",
    "Title": "The Shawshank Redemption",
    "Year": "1994",
    "Runtime": "142 min",
    "Genre": "Drama",
    "Director": "Frank Darabont",
    "Plot": "Two imprisoned men bond over a number of years.",
    "imdbRating": "9.3",
    "imdbVotes": "3,012,345",
    "imdbID": "tt0111161",
    "Type": "movie",
}

NOT_FOUND = {"Response": "False", "Error": "Movie not found!"}


class FakeClient(OmdbClient):
    """OmdbClient with canned responses instead of HTTP."""

    def __init__(self, responses: dict[tuple[tuple[str, str], ...], dict[str, Any]]) -> None:
        super().__init__("key")
        self.responses = responses
        self.requests: list[dict[str, str]] = []

    async def _fetch(self, params: dict[str, str]) -> dict[str, Any]:
        self.requests.append(params)
        resp = self.responses.get(tuple(sorted(params.items())), NOT_FOUND)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeAPI:
    def __init__(self) -> None:
        self.log = logging.getLogger("test.imdb")
        self.sent: list[tuple[str, str]] = []
        self.registered: list[str] = []

    async def privmsg(self, target: str, text: str) -> None:
        self.sent.append((target, text))

    def casefold(self, name: str) -> str:
        return name.lower()

    def get_bot_config(self) -> dict[str, Any]:
        return {"irc": {"admin": {"prefix": "~"}}}

    def register_channels(self, channels: list[str]) -> None:
        self.registered = list(channels)


def _module(client: OmdbClient, channels: list[Any] | None = None) -> tuple[ImdbModule, FakeAPI]:
    mod = ImdbModule()
    api = FakeAPI()
    mod.api = api  # type: ignore[assignment]
    mod.config = {"channels": channels or [], "cooldown_seconds": 0}
    mod.client = client
    mod._apply_config()
    return mod, api


def _say(mod: ImdbModule, text: str, target: str = "#movies", nick: str = "alice") -> None:
    asyncio.run(mod.on_privmsg(nick=nick, target=target, text=text))


def test_parse_query_splits_trailing_year_only_after_a_title() -> None:
    assert parse_query("blade runner 1982") == ("blade runner", "1982")
    assert parse_query("1917") == ("1917", None)
    assert parse_query("ocean's 11") == ("ocean's 11", None)


def test_find_imdb_ids_handles_variants_and_dedupes() -> None:
    text = (
        "see https://www.imdb.com/title/tt0111161/ and "
        "http://m.imdb.com/title/TT0111161/?ref=x and https://imdb.com/de/title/tt0068646"
    )
    assert find_imdb_ids(text) == ["tt0111161", "tt0068646"]
    assert find_imdb_ids("https://www.imdb.com/name/nm0000151/") == []


def test_format_title_movie() -> None:
    assert format_title(SHAWSHANK) == [
        "🎬 The Shawshank Redemption (1994) · ⭐ 9.3/10 (3.0M votes) · 142 min · Drama"
        " · Dir. Frank Darabont · https://www.imdb.com/title/tt0111161/"
    ]
    lines = format_title(SHAWSHANK, plot=True, link=False)
    assert not lines[0].endswith("/")
    assert lines[1] == SHAWSHANK["Plot"]


def test_format_title_series_skips_missing_fields() -> None:
    data = {
        "Title": "Breaking Bad",
        "Year": "2008–2013",
        "Type": "series",
        "totalSeasons": "5",
        "Director": "N/A",
        "imdbRating": "N/A",
        "Runtime": "49 min",
    }
    assert format_title(data, link=False) == ["📺 Breaking Bad (2008–2013) · 5 seasons · 49 min"]


def test_search_falls_back_to_full_title_when_year_misses() -> None:
    client = FakeClient({(("t", "blade runner 2049"),): {**SHAWSHANK, "Title": "Blade Runner 2049"}})
    data = asyncio.run(client.search("blade runner 2049"))
    assert data and data["Title"] == "Blade Runner 2049"
    assert client.requests == [{"t": "blade runner", "y": "2049"}, {"t": "blade runner 2049"}]


def test_lookups_are_cached_including_misses() -> None:
    client = FakeClient({(("i", "tt0111161"),): SHAWSHANK})
    asyncio.run(client.by_id("tt0111161"))
    asyncio.run(client.by_id("TT0111161"))
    asyncio.run(client.by_title("nonexistent"))
    asyncio.run(client.by_title("Nonexistent"))
    assert len(client.requests) == 2


def test_api_errors_raise_and_are_not_cached() -> None:
    client = FakeClient({(("t", "x"),): {"Response": "False", "Error": "Request limit reached!"}})
    with pytest.raises(OmdbError, match="Request limit"):
        asyncio.run(client.by_title("x"))
    assert client.cache == {}


def test_command_by_title_and_by_id() -> None:
    client = FakeClient({(("t", "shawshank"),): SHAWSHANK, (("i", "tt0111161"),): SHAWSHANK})
    mod, api = _module(client)
    _say(mod, "~imdb shawshank")
    _say(mod, "~imdb tt0111161", target="bot", nick="bob")
    assert [t for t, _ in api.sent] == ["#movies", "bob"]
    assert all("Shawshank" in text for _, text in api.sent)


def test_command_not_found_and_usage() -> None:
    mod, api = _module(FakeClient({}))
    _say(mod, "~imdb")
    _say(mod, "~imdb zzzz")
    assert api.sent[0][1].startswith("Usage: ~imdb")
    assert api.sent[1][1] == "❌ Nothing found for “zzzz”."


def test_command_reports_api_errors() -> None:
    mod, api = _module(FakeClient({(("t", "x"),): OmdbError("Invalid API key!")}))
    _say(mod, "~imdb x")
    assert api.sent == [("#movies", "❌ IMDb lookup failed: Invalid API key!")]


def test_previews_only_in_enabled_channels_without_link() -> None:
    client = FakeClient({(("i", "tt0111161"),): SHAWSHANK})
    mod, api = _module(client, ["#Movies", {"name": "#quiet", "previews": False}])
    assert api.registered == ["#Movies", "#quiet"]
    link = "lol https://www.imdb.com/title/tt0111161/"
    _say(mod, link, target="#movies")
    _say(mod, link, target="#quiet")
    _say(mod, link, target="#elsewhere")
    assert len(api.sent) == 1
    assert api.sent[0][0] == "#movies"
    assert "https://" not in api.sent[0][1]


def test_preview_errors_stay_quiet() -> None:
    mod, api = _module(FakeClient({(("i", "tt0111161"),): OmdbError("down")}), ["#movies"])
    _say(mod, "https://www.imdb.com/title/tt0111161/")
    assert api.sent == []


def test_cooldown_per_nick() -> None:
    client = FakeClient({(("t", "shawshank"),): SHAWSHANK})
    mod, api = _module(client)
    mod._cooldown = 60
    _say(mod, "~imdb shawshank")
    _say(mod, "~imdb shawshank")
    _say(mod, "~imdb shawshank", nick="bob")
    assert len(api.sent) == 2


def test_cache_survives_hot_reload() -> None:
    client = FakeClient({(("i", "tt0111161"),): SHAWSHANK})
    mod, _ = _module(client)
    asyncio.run(client.by_id("tt0111161"))
    fresh = ImdbModule()
    fresh.load_state(mod.get_state())
    assert fresh._pending_state["cache"] == client.cache
