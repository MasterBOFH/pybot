"""GitHub webhook module — report commits, issues, PRs to configured IRC channels."""

from __future__ import annotations

from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module
from pybot.modules.github.formatters import event_branch, format_event
from pybot.modules.github.webhook import make_handler


class GitHubModule(Module):
    name = "github"

    def __init__(self) -> None:
        super().__init__()
        self._secret = ""
        self._path = "/github"
        self._events: set[str] = set()
        self._emojis = True
        self._default_channels: list[str] = []
        self._repo_channels: dict[str, list[str]] = {}
        self._repo_branches: dict[str, list[str]] = {}

    def _apply_runtime_config(self) -> None:
        cfg = self.config or {}
        self._default_channels = []
        channel_cfg = cfg.get("channel")
        if isinstance(channel_cfg, str) and channel_cfg:
            self._default_channels = [channel_cfg]
        elif isinstance(channel_cfg, list):
            self._default_channels = [str(ch) for ch in channel_cfg if ch]
        self._repo_channels = self._parse_repo_channels(cfg)
        self._repo_branches = self._parse_repo_branches(cfg)
        self._secret = cfg.get("secret") or ""
        self._path = cfg.get("path") or "/github"
        events = cfg.get("events") or ["push", "issues", "pull_request"]
        self._events = set(events)
        self._emojis = bool(cfg.get("emojis", True))

    def _ensure_runtime_config(self) -> None:
        if not self._repo_channels or not self.config:
            self._apply_runtime_config()

    def _repo_channels_for(self, repo: str | None) -> list[str]:
        self._ensure_runtime_config()
        repo_key = repo.casefold() if isinstance(repo, str) else None
        if repo_key and repo_key in self._repo_channels:
            return list(self._repo_channels[repo_key])
        if self._default_channels:
            return list(self._default_channels)
        return []

    def _repo_branches_for(self, repo: str | None) -> list[str]:
        """Branch allowlist for ``repo``; empty means every branch is tracked."""
        self._ensure_runtime_config()
        repo_key = repo.casefold() if isinstance(repo, str) else None
        if repo_key and repo_key in self._repo_branches:
            return list(self._repo_branches[repo_key])
        return []

    def _branch_tracked(self, repo: str | None, branch: str | None) -> bool:
        """True unless the repo has a branch allowlist that excludes ``branch``.

        Events that carry no branch (``branch`` is None: issues, releases, tag
        pushes) are never filtered.
        """
        allowed = self._repo_branches_for(repo)
        if not allowed or branch is None:
            return True
        return branch in allowed

    @staticmethod
    def _iter_repo_configs(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        repos_cfg = cfg.get("repos") or []
        if not isinstance(repos_cfg, list):
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for repo_cfg in repos_cfg:
            if not isinstance(repo_cfg, dict):
                continue
            name = repo_cfg.get("name") or repo_cfg.get("repo")
            if not name:
                continue
            out.append((str(name).casefold(), repo_cfg))
        return out

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return []

    def _parse_repo_channels(self, cfg: dict[str, Any]) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for key, repo_cfg in self._iter_repo_configs(cfg):
            channels = repo_cfg.get("channels") or repo_cfg.get("channel")
            entries[key] = self._as_str_list(channels)
        return entries

    def _parse_repo_branches(self, cfg: dict[str, Any]) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for key, repo_cfg in self._iter_repo_configs(cfg):
            branches = repo_cfg.get("branches") or repo_cfg.get("branch")
            normalized = [b.strip() for b in self._as_str_list(branches) if b.strip()]
            if normalized:
                entries[key] = normalized
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
        await self.api.mount_route("POST", self._path, handler)
        repo_map = {
            repo_name: channels for repo_name, channels in sorted(self._repo_channels.items())
        }
        branch_map = {
            repo_name: branches for repo_name, branches in sorted(self._repo_branches.items())
        }
        self.api.log.info(
            "GitHub webhook listening on POST %s → default=%s repo_map=%s branches=%s events=%s emojis=%s",
            self._path,
            self._default_channels,
            repo_map,
            branch_map,
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
        self.api.log.debug(
            "Received supported GitHub event %s with payload repo=%s; dispatching repo lookup",
            event,
            repo,
        )
        repo_key = repo if repo != "<unknown>" else None
        branch = event_branch(event, payload)
        if not self._branch_tracked(repo_key, branch):
            self.api.log.debug(
                "GitHub event %s for repo %s on branch %s is not in the tracked branches %s; ignoring",
                event,
                repo,
                branch,
                self._repo_branches_for(repo_key),
            )
            return
        channels = self._repo_channels_for(repo_key)

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
