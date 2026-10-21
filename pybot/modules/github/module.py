"""GitHub webhook module — report commits, issues, PRs to configured IRC channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module
from pybot.modules.github.formatters import event_branch, format_event
from pybot.modules.github.webhook import make_handler

DEFAULT_EVENTS = ("push", "issues", "pull_request")
ALL_REPOS = "*"


@dataclass(frozen=True)
class ChannelRoute:
    """One ``channels:`` entry: which repos and events a channel receives."""

    name: str
    repos: frozenset[str] | None  # casefolded repo names; None = every repo
    events: frozenset[str]

    def wants(self, event: str, repo: str | None) -> bool:
        if event not in self.events:
            return False
        if self.repos is None:
            return True
        return repo is not None and repo.casefold() in self.repos


class GitHubModule(Module):
    name = "github"

    def __init__(self) -> None:
        super().__init__()
        self._secret = ""
        self._path = "/github"
        self._emojis = True
        self._routes: list[ChannelRoute] = []
        self._repo_branches: dict[str, list[str]] = {}

    def _apply_runtime_config(self) -> None:
        cfg = self.config or {}
        default_events = self._as_str_list(cfg.get("events")) or list(DEFAULT_EVENTS)
        self._routes = self._parse_routes(cfg, default_events)
        self._repo_branches = self._parse_repo_branches(cfg)
        self._secret = cfg.get("secret") or ""
        self._path = cfg.get("path") or "/github"
        self._emojis = bool(cfg.get("emojis", True))

    def _ensure_runtime_config(self) -> None:
        if not self._routes or not self.config:
            self._apply_runtime_config()

    def _channels_for(self, event: str, repo: str | None) -> list[str]:
        """Channels subscribed to ``event`` for ``repo``, in config order, deduplicated."""
        self._ensure_runtime_config()
        seen: set[str] = set()
        out: list[str] = []
        for route in self._routes:
            if route.wants(event, repo) and route.name.casefold() not in seen:
                seen.add(route.name.casefold())
                out.append(route.name)
        return out

    def _enabled_events(self) -> set[str]:
        """Every event type at least one channel subscribes to."""
        self._ensure_runtime_config()
        return {event for route in self._routes for event in route.events}

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
    def _as_str_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return []

    def _parse_routes(self, cfg: dict[str, Any], default_events: list[str]) -> list[ChannelRoute]:
        channels_cfg = cfg.get("channels") or []
        if not isinstance(channels_cfg, list):
            return []
        routes: list[ChannelRoute] = []
        for channel_cfg in channels_cfg:
            if not isinstance(channel_cfg, dict):
                continue
            name = str(channel_cfg.get("name") or "").strip()
            if not name:
                continue
            repo_names = [r.strip() for r in self._as_str_list(channel_cfg.get("repos")) if r.strip()]
            repos = None if ALL_REPOS in repo_names else frozenset(r.casefold() for r in repo_names)
            events = self._as_str_list(channel_cfg.get("events")) or default_events
            routes.append(ChannelRoute(name=name, repos=repos, events=frozenset(events)))
        return routes

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

    def _parse_repo_branches(self, cfg: dict[str, Any]) -> dict[str, list[str]]:
        entries: dict[str, list[str]] = {}
        for key, repo_cfg in self._iter_repo_configs(cfg):
            branches = repo_cfg.get("branches") or repo_cfg.get("branch")
            normalized = [b.strip() for b in self._as_str_list(branches) if b.strip()]
            if normalized:
                entries[key] = normalized
        return entries

    def _warn_legacy_config(self) -> None:
        """The old repo→channel mapping is no longer read; say so instead of going quiet."""
        assert self.api is not None
        cfg = self.config or {}
        legacy = cfg.get("channel") is not None or any(
            "channels" in repo_cfg or "channel" in repo_cfg for _, repo_cfg in self._iter_repo_configs(cfg)
        )
        if legacy:
            self.api.log.warning(
                "github: 'channel' and 'repos[].channels' are no longer supported; "
                "move channel routing to 'channels:' (name/repos/events)"
            )

    def _configured_channels(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for route in self._routes:
            if route.name.casefold() in seen:
                continue
            seen.add(route.name.casefold())
            ordered.append(route.name)
        return ordered

    def _log_routes(self) -> None:
        assert self.api is not None
        routes = [
            f"{route.name}(repos={ALL_REPOS if route.repos is None else sorted(route.repos)}, events={sorted(route.events)})"
            for route in self._routes
        ]
        branch_map = {
            repo_name: branches for repo_name, branches in sorted(self._repo_branches.items())
        }
        self.api.log.info(
            "GitHub webhook listening on POST %s → channels=%s branches=%s emojis=%s",
            self._path,
            routes,
            branch_map,
            self._emojis,
        )

    async def reload_config(self, config: dict[str, Any]) -> None:
        await super().reload_config(config)
        self.config = config
        self._apply_runtime_config()
        if self.api is not None:
            self._warn_legacy_config()
            self.api.register_channels(self._configured_channels())
            self._log_routes()

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        self._apply_runtime_config()
        self._warn_legacy_config()
        self.api.register_channels(self._configured_channels())

        if not self._secret:
            self.api.log.warning("github: no secret configured")

        handler = make_handler(
            secret=self._secret,
            allowed_events=self._enabled_events,
            on_event=self._on_github_event,
        )
        await self.api.mount_route("POST", self._path, handler)
        self._log_routes()

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
        channels = self._channels_for(event, repo_key)

        self.api.log.debug(
            "Received supported GitHub event %s for repo %s; posting to %s",
            event,
            repo,
            channels,
        )

        if not channels:
            self.api.log.debug(
                "GitHub event %s for repo %s had no subscribed channels; ignoring",
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
