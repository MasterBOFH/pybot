"""IMDb module — ~imdb title lookups and IMDb link previews via OMDb."""

from __future__ import annotations

import time
from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module, on
from pybot.modules.imdb.omdb import (
    IMDB_ID_RE,
    OmdbClient,
    OmdbError,
    find_imdb_ids,
    format_title,
)

# Most links previewed from a single message; the rest are ignored.
MAX_PREVIEWS = 3


class ImdbModule(Module):
    name = "imdb"

    def __init__(self) -> None:
        super().__init__()
        self.client: OmdbClient | None = None
        self._pending_state: dict[str, Any] = {}
        # casefolded channel names that get link previews
        self._preview_channels: set[str] = set()
        self._channels: list[str] = []
        self._cmd_prefix = "~"
        self._plot = False
        self._cooldown = 5.0
        # casefolded nick -> monotonic time of their last lookup
        self._last_use: dict[str, float] = {}

    def load_state(self, state: dict[str, Any]) -> None:
        self._pending_state = state or {}

    def get_state(self) -> dict[str, Any]:
        if not self.client:
            return {}
        return {"cache": self.client.cache.copy()}

    @staticmethod
    def _parse_channels(cfg: dict[str, Any]) -> list[tuple[str, bool]]:
        """Return (channel, previews). previews defaults to true."""
        channels_cfg = cfg.get("channels")
        if not isinstance(channels_cfg, list):
            return []
        out: list[tuple[str, bool]] = []
        for entry in channels_cfg:
            if isinstance(entry, str):
                out.append((entry, True))
                continue
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("channel")
            if not name:
                continue
            out.append((str(name), bool(entry.get("previews", True))))
        return out

    def _apply_config(self) -> None:
        assert self.api is not None
        cfg = self.config
        channels = self._parse_channels(cfg)
        self._channels = [name for name, _previews in channels]
        self._preview_channels = {
            self.api.casefold(name) for name, previews in channels if previews
        }
        admin = (self.api.get_bot_config().get("irc") or {}).get("admin") or {}
        self._cmd_prefix = cfg.get("command_prefix") or admin.get("prefix") or "~"
        self._plot = bool(cfg.get("plot", False))
        self._cooldown = float(cfg.get("cooldown_seconds", 5))
        if self.client is not None:
            self.client.timeout = float(cfg.get("timeout_seconds") or 5)
            self.client.cache_seconds = float(cfg.get("cache_minutes") or 1440) * 60
        self.api.register_channels(self._channels)

    async def reload_config(self, config: dict[str, Any]) -> None:
        await super().reload_config(config)
        self.config = config
        api_key = config.get("api_key") or ""
        if self.client is not None and api_key:
            self.client.api_key = api_key
        self._apply_config()

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        api_key = self.config.get("api_key") or ""
        if not api_key:
            self.api.log.error("imdb: api_key required (free key at omdbapi.com)")
        else:
            self.client = OmdbClient(api_key, cache=self._pending_state.get("cache"))
            self._pending_state = {}
        self._apply_config()
        self.api.log.info(
            "IMDb ready (command: %simdb, previews in: %s)",
            self._cmd_prefix,
            ", ".join(n for n in self._channels if self.api.casefold(n) in self._preview_channels)
            or "none",
        )

    async def teardown(self) -> None:
        if self.api:
            self.api.unregister_channels()
        await super().teardown()

    def _throttled(self, nick: str) -> bool:
        """True if ``nick`` looked something up within the cooldown; else records this use."""
        assert self.api is not None
        key = self.api.casefold(nick)
        now = time.monotonic()
        last = self._last_use.get(key)
        if last is not None and now - last < self._cooldown:
            return True
        self._last_use = {k: t for k, t in self._last_use.items() if now - t < self._cooldown}
        self._last_use[key] = now
        return False

    @on("privmsg")
    async def on_privmsg(
        self,
        nick: str | None = None,
        target: str | None = None,
        text: str | None = None,
        **_kwargs: Any,
    ) -> None:
        assert self.api is not None
        text = text or ""
        if not nick or self.client is None:
            return
        is_chan = bool(target and target[:1] in ("#", "&"))
        reply_to = target if is_chan else nick
        if not reply_to:
            return

        if text.startswith(self._cmd_prefix):
            body = text[len(self._cmd_prefix) :].strip()
            cmd, _, query = body.partition(" ")
            if cmd.lower() == "imdb":
                await self._cmd_imdb(nick, reply_to, query.strip())
            return

        if is_chan and self.api.casefold(target or "") in self._preview_channels:
            ids = find_imdb_ids(text)[:MAX_PREVIEWS]
            if ids and not self._throttled(nick):
                await self._previews(reply_to, ids)

    async def _cmd_imdb(self, nick: str, reply_to: str, query: str) -> None:
        assert self.api is not None and self.client is not None
        if not query:
            await self.api.privmsg(reply_to, f"Usage: {self._cmd_prefix}imdb <title> [year] | <tt-id>")
            return
        if self._throttled(nick):
            return
        try:
            if IMDB_ID_RE.match(query):
                data = await self.client.by_id(query)
            else:
                data = await self.client.search(query)
        except OmdbError as exc:
            self.api.log.warning("imdb lookup %r failed: %s", query, exc)
            await self.api.privmsg(reply_to, f"❌ IMDb lookup failed: {exc}")
            return
        if data is None:
            await self.api.privmsg(reply_to, f"❌ Nothing found for “{query}”.")
            return
        for line in format_title(data, plot=self._plot):
            await self.api.privmsg(reply_to, line)

    async def _previews(self, channel: str, ids: list[str]) -> None:
        """Summarize linked titles. Failures stay in the log: nobody asked for these."""
        assert self.api is not None and self.client is not None
        for imdb_id in ids:
            try:
                data = await self.client.by_id(imdb_id)
            except OmdbError as exc:
                self.api.log.warning("imdb preview %s failed: %s", imdb_id, exc)
                return
            if data is None:
                continue
            # The link is already in the channel, so don't repeat it.
            await self.api.privmsg(channel, format_title(data, link=False)[0])
