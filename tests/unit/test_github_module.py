from __future__ import annotations

import asyncio

from pybot.modules.github.module import GitHubModule


class FakeAPI:
    def __init__(self) -> None:
        self.log = type("L", (), {"debug": lambda *a, **k: None, "info": lambda *a, **k: None, "warning": lambda *a, **k: None})()
        self.sent: list[tuple[str, str]] = []

    async def privmsg(self, channel: str, text: str) -> None:
        self.sent.append((channel, text))


def test_repo_channels_mapping_uses_default_when_missing() -> None:
    mod = GitHubModule()
    mod.config = {
        "channel": "#default",
        "repos": [
            {"name": "org/repo1", "channels": ["#dev", "#ops"]},
            {"name": "org/repo2", "channels": ["#release"]},
        ],
    }

    assert mod._repo_channels_for("org/repo1") == ["#dev", "#ops"]
    assert mod._repo_channels_for("org/repo2") == ["#release"]
    assert mod._repo_channels_for("org/other") == ["#default"]


def test_event_sends_only_to_configured_repo_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [
            {"name": "org/repo1", "channels": ["#dev", "#ops"]},
            {"name": "org/repo2", "channels": ["#release"]},
        ],
    }
    mod._events = {"push"}
    mod._emojis = False
    api = FakeAPI()
    mod.api = api

    asyncio.run(
        mod._on_github_event(
            "push",
            {
                "ref": "refs/heads/main",
                "repository": {"full_name": "org/repo1"},
                "pusher": {"name": "alice"},
                "commits": [
                    {
                        "id": "abcdef123456",
                        "author": {"name": "alice"},
                        "message": "fix bug",
                    }
                ],
            },
        )
    )

    sent_channels = [channel for channel, _ in api.sent]
    assert sent_channels.count("#dev") == 2
    assert sent_channels.count("#ops") == 2
    assert set(sent_channels) == {"#dev", "#ops"}
