"""GitHub webhook module — report commits, issues, PRs to IRC."""

from __future__ import annotations

from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module
from pybot.modules.github_webhook.formatters import format_event
from pybot.modules.github_webhook.webhook import make_handler


class GithubWebhookModule(Module):
    name = "github_webhook"

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        cfg = self.config
        self._channel = cfg.get("channel") or "#dev"
        self._secret = cfg.get("secret") or ""
        self._path = cfg.get("path") or "/github"
        events = cfg.get("events") or ["push", "issues", "pull_request"]
        self._events = set(events)
        self._emojis = bool(cfg.get("emojis", True))

        if not self._secret:
            self.api.log.warning("github_webhook: no secret configured")

        handler = make_handler(
            secret=self._secret,
            allowed_events=self._events,
            on_event=self._on_github_event,
        )
        self.api.mount_route("POST", self._path, handler)
        self.api.log.info(
            "GitHub webhook listening on POST %s → %s events=%s emojis=%s",
            self._path,
            self._channel,
            sorted(self._events),
            self._emojis,
        )

    async def teardown(self) -> None:
        if self.api:
            self.api.unmount_routes()
        await super().teardown()

    async def _on_github_event(self, event: str, payload: dict[str, Any]) -> None:
        assert self.api is not None
        lines = format_event(event, payload, emojis=self._emojis)
        if not lines:
            self.api.log.debug("No IRC lines for event %s", event)
            return
        for line in lines:
            await self.api.privmsg(self._channel, line)
        self.api.log.info("Reported GitHub %s (%d lines)", event, len(lines))
