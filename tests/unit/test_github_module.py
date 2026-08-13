from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pybot.modules.github.webhook as github_webhook
from pybot.modules.github.module import GitHubModule
from pybot.modules.github.webhook import make_handler


class CapturingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def exception(self, message: str, *args: object) -> None:
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


def test_unconfigured_repo_has_no_implicit_default_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [{"name": "org/repo1", "channels": ["#dev"]}],
    }

    assert mod._repo_channels_for("org/repo1") == ["#dev"]
    assert mod._repo_channels_for("org/other") == []
    assert mod._default_channels == []


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


def test_fake_webhook_handler_accepts_signed_push_event() -> None:
    secret = "test-secret"
    payload = {
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
    }
    body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    class FakeRequest:
        def __init__(self, body_bytes: bytes, headers: dict[str, str]) -> None:
            self._body = body_bytes
            self.headers = headers
            self.remote = "127.0.0.1"

        async def read(self) -> bytes:
            return self._body

    seen: dict[str, object] = {}

    async def on_event(event: str, received: dict[str, object]) -> None:
        seen["event"] = event
        seen["repo"] = received.get("repository", {}).get("full_name")

    handler = make_handler(secret=secret, allowed_events={"push"}, on_event=on_event)
    response = asyncio.run(
        handler(
            FakeRequest(
                body,
                {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
            )
        )
    )

    assert response.status == 200
    assert seen["event"] == "push"
    assert seen["repo"] == "org/repo1"


def test_invalid_json_logs_body_preview(monkeypatch) -> None:
    secret = "test-secret"
    body = b"not-json"
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    class FakeRequest:
        def __init__(self, body_bytes: bytes, headers: dict[str, str]) -> None:
            self._body = body_bytes
            self.headers = headers
            self.remote = "127.0.0.1"

        async def read(self) -> bytes:
            return self._body

    logger = CapturingLogger()
    monkeypatch.setattr(github_webhook, "log", logger)

    handler = make_handler(secret=secret, allowed_events={"push"}, on_event=lambda *_args, **_kwargs: None)
    response = asyncio.run(
        handler(
            FakeRequest(
                body,
                {
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Event": "push",
                    "Content-Type": "application/json",
                    "Content-Encoding": "identity",
                },
            )
        )
    )

    assert response.status == 400
    logged = "\n".join(logger.messages)
    assert "payload_preview" in logged
    assert "not-json" in logged
