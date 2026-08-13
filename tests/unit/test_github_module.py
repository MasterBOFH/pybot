from __future__ import annotations

import asyncio

from pybot.modules.github.module import GitHubModule


class CapturingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)


class FakeAPI:
    def __init__(self) -> None:
        self.log = CapturingLogger()
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


def test_push_event_debug_logs_repo_and_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [
            {"name": "org/repo1", "channels": ["#dev", "#ops"]},
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

    log_lines = "\n".join(api.log.messages)
    assert "Received supported GitHub event push" in log_lines
    assert "org/repo1" in log_lines
    assert "#dev" in log_lines and "#ops" in log_lines


def test_repo_channels_match_case_insensitively() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [{"name": "Org/Repo1", "channels": ["#dev"]}],
    }

    assert mod._repo_channels_for("org/repo1") == ["#dev"]
    assert mod._repo_channels_for("ORG/REPO1") == ["#dev"]
