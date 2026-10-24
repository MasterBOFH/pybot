"""OMDb API client and IRC formatting for IMDb title lookups."""

from __future__ import annotations

import re
import time
from typing import Any

OMDB_URL = "https://www.omdbapi.com/"
IMDB_TITLE_URL = "https://www.imdb.com/title/{}/"

IMDB_ID_RE = re.compile(r"^tt\d{7,10}$", re.IGNORECASE)
# imdb.com, www.imdb.com, m.imdb.com, plus localized paths like /de/title/…
IMDB_LINK_RE = re.compile(
    r"https?://(?:[a-z]+\.)?imdb\.com/(?:[a-z]{2}/)?title/(tt\d{7,10})",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"^(18[89]\d|19\d\d|20\d\d)$")

MAX_PLOT_CHARS = 300


class OmdbError(Exception):
    """Lookup failed for a reason worth showing on IRC (bad key, quota, network)."""


def parse_query(query: str) -> tuple[str, str | None]:
    """Split ``"blade runner 1982"`` into ``("blade runner", "1982")``.

    A trailing four-digit year is only split off when something is left in
    front of it, so ``"1917"`` stays a title.
    """
    parts = query.split()
    if len(parts) > 1 and YEAR_RE.match(parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return query.strip(), None


def find_imdb_ids(text: str) -> list[str]:
    """Distinct IMDb title IDs linked in ``text``, in order of appearance."""
    out: list[str] = []
    for match in IMDB_LINK_RE.finditer(text):
        imdb_id = match.group(1).lower()
        if imdb_id not in out:
            out.append(imdb_id)
    return out


def _field(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    return "" if value == "N/A" else value


def _compact_votes(votes: str) -> str:
    try:
        n = int(votes.replace(",", ""))
    except ValueError:
        return votes
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K" if n >= 10_000 else f"{n / 1_000:.1f}K"
    return str(n)


def format_title(data: dict[str, Any], *, plot: bool = False, link: bool = True) -> list[str]:
    """IRC lines for one OMDb title record."""
    kind = _field(data, "Type")
    title = _field(data, "Title") or "?"
    year = _field(data, "Year")
    head = f"{'📺' if kind == 'series' else '🎬'} {title}"
    if year:
        head += f" ({year})"
    parts = [head]

    rating = _field(data, "imdbRating")
    if rating:
        votes = _field(data, "imdbVotes")
        parts.append(f"⭐ {rating}/10" + (f" ({_compact_votes(votes)} votes)" if votes else ""))
    seasons = _field(data, "totalSeasons")
    if kind == "series" and seasons:
        parts.append(f"{seasons} season{'' if seasons == '1' else 's'}")
    for key in ("Runtime", "Genre"):
        value = _field(data, key)
        if value:
            parts.append(value)
    director = _field(data, "Director")
    if director:
        parts.append(f"Dir. {director}")
    imdb_id = _field(data, "imdbID")
    if link and imdb_id:
        parts.append(IMDB_TITLE_URL.format(imdb_id))

    lines = [" · ".join(parts)]
    text = _field(data, "Plot")
    if plot and text:
        if len(text) > MAX_PLOT_CHARS:
            text = text[: MAX_PLOT_CHARS - 1].rstrip() + "…"
        lines.append(text)
    return lines


class OmdbClient:
    """Looks titles up on OMDb, caching results (including misses) for ``cache_seconds``."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 5.0,
        cache_seconds: float = 86400.0,
        cache: dict[str, tuple[float, dict[str, Any] | None]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        # cache key -> (monotonic expiry, record or None for "not found")
        self.cache: dict[str, tuple[float, dict[str, Any] | None]] = cache or {}

    async def by_id(self, imdb_id: str) -> dict[str, Any] | None:
        return await self._lookup({"i": imdb_id.lower()})

    async def by_title(self, title: str, year: str | None = None) -> dict[str, Any] | None:
        params = {"t": title}
        if year:
            params["y"] = year
        return await self._lookup(params)

    async def search(self, query: str) -> dict[str, Any] | None:
        """Best match for a free-text query, with an optional trailing year.

        ``"blade runner 2049"`` is first tried as title + year, then as the
        full title when that finds nothing.
        """
        title, year = parse_query(query)
        result = await self.by_title(title, year)
        if result is None and year:
            result = await self.by_title(query.strip())
        return result

    async def _lookup(self, params: dict[str, str]) -> dict[str, Any] | None:
        key = "&".join(f"{k}={v.casefold()}" for k, v in sorted(params.items()))
        now = time.monotonic()
        hit = self.cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

        data = await self._fetch(params)
        if str(data.get("Response")) == "True":
            result: dict[str, Any] | None = data
        else:
            error = str(data.get("Error") or "unknown error")
            if "not found" not in error.lower() and "incorrect imdb id" not in error.lower():
                raise OmdbError(error)
            result = None
        self._prune(now)
        self.cache[key] = (now + self.cache_seconds, result)
        return result

    async def _fetch(self, params: dict[str, str]) -> dict[str, Any]:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=max(self.timeout, 1.0))
        query = {"apikey": self.api_key, "plot": "short", **params}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(OMDB_URL, params=query) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and "Response" in data:
                        # OMDb reports a bad key / exhausted quota as a 401
                        # with a JSON body, so prefer its own error text.
                        return data
                    raise OmdbError(f"OMDb returned HTTP {resp.status}")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise OmdbError(f"OMDb unreachable ({type(exc).__name__})") from exc

    def _prune(self, now: float) -> None:
        for key in [k for k, (expiry, _) in self.cache.items() if expiry <= now]:
            del self.cache[key]
