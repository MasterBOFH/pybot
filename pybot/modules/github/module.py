"""GitHub webhook module — report commits, issues, PRs to configured IRC channels."""

from __future__ import annotations

from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module
from pybot.modules.github.formatters import format_event
from pybot.modules.github.webhook import make_handler


class GitHubModule(Module):
    name = "github"

    def __init__(self) -> None:
        super().__init__()
        self._secret = ""
        self._path = "/github"
        self._events: set[str] = set()
        self._emojis = True
        self._default_channels: list[str] = ["#dev"]
        self._repo_channels: dict[str, list[str]] = {}

    def _apply_runtime_config(self) -> None:
        cfg = self.config or {}
        self._default_channels = []
        channel_cfg = cfg.get("channel")
        if isinstance(channel_cfg, str) and channel_cfg:
            self._default_channels = [channel_cfg]
        elif isinstance(channel_cfg, list):
            self._default_channels = [str(ch) for ch in channel_cfg if ch]
        if not self._default_channels:
            self._default_channels = ["#dev"]
        self._repo_channels = self._parse_repo_channels(cfg)
        self._secret = cfg.get("secret") or ""
        self._path = cfg.get("path") or "/github"
        events = cfg.get("events") or ["push", "issues", "pull_request"]
        self._events = set(events)
        self._emojis = bool(cfg.get("emojis", True))

    def _repo_channels_for(self, repo: str | None) -> list[str]:
        if not self._repo_channels or not self.config:
            self._apply_runtime_config()
        repo_key = repo.casefold() if isinstance(repo, str) else None
        if repo_key and repo_key in self._repo_channels:
            return list(self._repo_channels[repo_key])
        if self._default_channels:
            return list(self._default_channels)
        return []

    def _parse_repo_channels(self, cfg: dict[str, Any]) -> dict[str, list[str]]:
        repos_cfg = cfg.get("repos") or []
        if not isinstance(repos_cfg, list):
            return {}

        entries: dict[str, list[str]] = {}
        for repo_cfg in repos_cfg:
            if not isinstance(repo_cfg, dict):
                continue
            name = repo_cfg.get("name") or repo_cfg.get("repo")
            if not name:
                continue
            channels = repo_cfg.get("channels") or repo_cfg.get("channel")
            if isinstance(channels, str):
                normalized = [channels]
            elif isinstance(channels, list):
                normalized = [str(ch) for ch in channels if ch]
            else:
                normalized = []
            if not normalized:
                normalized = [str(cfg.get("channel") or "#dev")]
            entries[str(name).casefold()] = normalized
        return entries

    def _configured_channels(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for channel in [*self._default_channels, *[ch for channels in self._repo_channels.values() for ch in channels]]:
            channel = str(channel).strip()
            if not channel or channel.lower() in {item.lower() for item in seen}:
                continue
            seen.add(channel)
            ordered.append(channel)
        return ordered

    async def reload_config(self, config: dict[str, Any]) -> None:
        await super().reload_config(config)
        self.config = config
        self._apply_runtime_config()
        if self.api is not None:
            self.api.register_channels(self._configured_channels())

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        self._apply_runtime_config()
        self.api.register_channels(self._configured_channels())

        if not self._secret:
            self.api.log.warning("github: no secret configured")

        handler = make_handler(
            secret=self._secret,
            allowed_events=self._events,
            on_event=self._on_github_event,
        )
        self.api.mount_route("POST", self._path, handler)
        self.api.log.info(
            "GitHub webhook listening on POST %s → default=%s repos=%s events=%s emojis=%s",
            self._path,
            self._default_channels,
            len(self._repo_channels),
            sorted(self._events),
            self._emojis,
        )

    async def teardown(self) -> None:
        if self.api:
            self.api.unregister_channels()
            self.api.unmount_routes()
        await super().teardown()

    async def _on_github_event(self, event: str, payload: dict[str, Any]) -> None:
        assert self.api is not None
        repo = (payload.get("repository") or {}).get("full_name") or "<unknown>"
        channels = self._repo_channels_for(repo if repo != "<unknown>" else None)

        self.api.log.debug(
            "Received supported GitHub event %s for repo %s; posting to %s",
            event,
            repo,
            channels,
        )

        if not channels:
            self.api.log.debug(
                "GitHub event %s for repo %s had no configured channels; ignoring",
                event,
                repo,
            )
            return

        lines = format_event(event, payload, emojis=self._emojis)
        if not lines:
            self.api.log.debug("No IRC lines for event %s on repo %s", event, repo)
            return
        for channel in channels:
            for line in lines:
                await self.api.privmsg(channel, line)
        self.api.log.info("Reported GitHub %s for %s to %s (%d lines)", event, repo, channels, len(lines))
