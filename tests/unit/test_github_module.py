from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pybot.modules.github.webhook as github_webhook
from pybot.modules.github.formatters import event_branch
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


def test_workflow_run_failure_reports_to_configured_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [
            {"name": "org/repo1", "channels": ["#dev", "#ops"]},
        ],
    }
    mod._events = {"workflow_run"}
    mod._emojis = False
    api = FakeAPI()
    mod.api = api

    asyncio.run(
        mod._on_github_event(
            "workflow_run",
            {
                "action": "completed",
                "repository": {"full_name": "org/repo1"},
                "workflow_run": {
                    "name": "CI",
                    "run_number": 7,
                    "head_branch": "main",
                    "conclusion": "failure",
                    "html_url": "https://example/runs/7",
                },
            },
        )
    )

    sent_channels = [channel for channel, _ in api.sent]
    assert set(sent_channels) == {"#dev", "#ops"}
    assert any("failed" in text for _, text in api.sent)


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


def _push_payload(repo: str, ref: str) -> dict:
    return {
        "ref": ref,
        "repository": {"full_name": repo},
        "pusher": {"name": "alice"},
        "commits": [{"id": "abcdef123456", "author": {"name": "alice"}, "message": "fix bug"}],
    }


def _run_event(mod: GitHubModule, event: str, payload: dict) -> FakeAPI:
    api = FakeAPI()
    mod.api = api
    mod._emojis = False
    asyncio.run(mod._on_github_event(event, payload))
    return api


def test_event_branch_extraction() -> None:
    assert event_branch("push", {"ref": "refs/heads/main"}) == "main"
    assert event_branch("push", {"ref": "refs/heads/feature/foo"}) == "feature/foo"
    assert event_branch("push", {"ref": "refs/tags/v1.0"}) is None
    assert event_branch("pull_request", {"pull_request": {"base": {"ref": "dev"}, "head": {"ref": "x"}}}) == "dev"
    assert event_branch("workflow_run", {"workflow_run": {"head_branch": "ci"}}) == "ci"
    assert event_branch("issues", {"issue": {"number": 1}}) is None
    assert event_branch("release", {"release": {"tag_name": "v1"}}) is None


def test_repo_without_branches_tracks_every_branch() -> None:
    mod = GitHubModule()
    mod.config = {"repos": [{"name": "org/repo1", "channels": ["#dev"]}]}

    assert mod._repo_branches_for("org/repo1") == []
    for ref in ("refs/heads/main", "refs/heads/feature/foo"):
        api = _run_event(mod, "push", _push_payload("org/repo1", ref))
        assert {ch for ch, _ in api.sent} == {"#dev"}


def test_repo_branches_filter_push_events() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [{"name": "org/repo1", "channels": ["#dev"], "branches": ["main", "release/1.x"]}],
    }

    assert mod._repo_branches_for("org/repo1") == ["main", "release/1.x"]

    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/main"))
    assert {ch for ch, _ in api.sent} == {"#dev"}

    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/release/1.x"))
    assert {ch for ch, _ in api.sent} == {"#dev"}

    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/feature/foo"))
    assert api.sent == []
    assert any("not in the tracked branches" in m for m in api.log.messages)


def test_repo_branches_single_string_shorthand() -> None:
    mod = GitHubModule()
    mod.config = {"repos": [{"name": "org/repo1", "channels": ["#dev"], "branch": "main"}]}

    assert mod._repo_branches_for("org/repo1") == ["main"]
    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/dev"))
    assert api.sent == []


def test_repo_branches_do_not_filter_tags_issues_or_releases() -> None:
    mod = GitHubModule()
    mod.config = {"repos": [{"name": "org/repo1", "channels": ["#dev"], "branches": ["main"]}]}

    tag_push = {
        "ref": "refs/tags/v1.0",
        "after": "abcdef123456",
        "repository": {"full_name": "org/repo1", "html_url": "https://example/org/repo1"},
        "pusher": {"name": "alice"},
        "commits": [],
    }
    api = _run_event(mod, "push", tag_push)
    assert {ch for ch, _ in api.sent} == {"#dev"}

    issue = {
        "action": "opened",
        "repository": {"full_name": "org/repo1"},
        "sender": {"login": "alice"},
        "issue": {"number": 3, "title": "bug", "html_url": "https://example/3"},
    }
    api = _run_event(mod, "issues", issue)
    assert {ch for ch, _ in api.sent} == {"#dev"}

    release = {
        "action": "published",
        "repository": {"full_name": "org/repo1"},
        "sender": {"login": "alice"},
        "release": {"tag_name": "v1.0", "name": "v1.0", "html_url": "https://example/r/1"},
    }
    api = _run_event(mod, "release", release)
    assert {ch for ch, _ in api.sent} == {"#dev"}


def test_repo_branches_filter_pull_request_by_base_branch() -> None:
    mod = GitHubModule()
    mod.config = {"repos": [{"name": "org/repo1", "channels": ["#dev"], "branches": ["main"]}]}

    def pr(base: str) -> dict:
        return {
            "action": "opened",
            "repository": {"full_name": "org/repo1"},
            "sender": {"login": "alice"},
            "pull_request": {
                "number": 9,
                "title": "change",
                "html_url": "https://example/pr/9",
                "base": {"ref": base},
                "head": {"ref": "feature/foo"},
            },
        }

    api = _run_event(mod, "pull_request", pr("main"))
    assert {ch for ch, _ in api.sent} == {"#dev"}

    api = _run_event(mod, "pull_request", pr("dev"))
    assert api.sent == []


def test_repo_branches_filter_workflow_run_by_head_branch() -> None:
    mod = GitHubModule()
    mod.config = {"repos": [{"name": "org/repo1", "channels": ["#dev"], "branches": ["main"]}]}

    def run(branch: str) -> dict:
        return {
            "action": "completed",
            "repository": {"full_name": "org/repo1"},
            "workflow_run": {
                "name": "CI",
                "run_number": 7,
                "head_branch": branch,
                "conclusion": "success",
                "html_url": "https://example/runs/7",
            },
        }

    api = _run_event(mod, "workflow_run", run("main"))
    assert {ch for ch, _ in api.sent} == {"#dev"}

    api = _run_event(mod, "workflow_run", run("feature/foo"))
    assert api.sent == []


def test_branch_filter_is_per_repo_and_case_insensitive_on_repo_name() -> None:
    mod = GitHubModule()
    mod.config = {
        "repos": [
            {"name": "Org/Repo1", "channels": ["#dev"], "branches": ["main"]},
            {"name": "org/repo2", "channels": ["#ops"]},
        ],
    }

    api = _run_event(mod, "push", _push_payload("ORG/REPO1", "refs/heads/dev"))
    assert api.sent == []

    api = _run_event(mod, "push", _push_payload("org/repo2", "refs/heads/dev"))
    assert {ch for ch, _ in api.sent} == {"#ops"}


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
