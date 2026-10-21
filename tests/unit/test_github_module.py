from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pybot.modules.github.webhook as github_webhook
from pybot.modules.github.formatters import event_branch
from pybot.modules.github.module import GitHubModule
from pybot.modules.github.webhook import make_handler

ALL_EVENTS = ["push", "issues", "pull_request", "release", "workflow_run"]


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
        self.registered: list[str] = []

    async def privmsg(self, channel: str, text: str) -> None:
        self.sent.append((channel, text))

    def register_channels(self, channels: list[str]) -> None:
        self.registered = list(channels)

    def unregister_channels(self) -> None:
        self.registered = []


def _push_payload(repo: str, ref: str) -> dict:
    return {
        "ref": ref,
        "repository": {"full_name": repo},
        "pusher": {"name": "alice"},
        "commits": [{"id": "abcdef123456", "author": {"name": "alice"}, "message": "fix bug"}],
    }


def _issue_payload(repo: str) -> dict:
    return {
        "action": "opened",
        "repository": {"full_name": repo},
        "sender": {"login": "alice"},
        "issue": {"number": 3, "title": "bug", "html_url": "https://example/3"},
    }


def _release_payload(repo: str) -> dict:
    return {
        "action": "published",
        "repository": {"full_name": repo},
        "sender": {"login": "alice"},
        "release": {"tag_name": "v1.0", "name": "v1.0", "html_url": "https://example/r/1"},
    }


def _run_event(mod: GitHubModule, event: str, payload: dict) -> FakeAPI:
    api = FakeAPI()
    mod.api = api
    mod._emojis = False
    asyncio.run(mod._on_github_event(event, payload))
    return api


def _sent_to(api: FakeAPI) -> set[str]:
    return {ch for ch, _ in api.sent}


def test_channels_receive_only_their_repos() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1", "org/repo2"]},
            {"name": "#ops", "repos": ["org/repo1"]},
            {"name": "#release", "repos": ["org/repo2"]},
        ],
    }

    assert mod._channels_for("push", "org/repo1") == ["#dev", "#ops"]
    assert mod._channels_for("push", "org/repo2") == ["#dev", "#release"]
    assert mod._channels_for("push", "org/other") == []
    assert mod._channels_for("push", None) == []


def test_channel_events_are_per_channel() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"], "events": ["push", "pull_request"]},
            {"name": "#release", "repos": ["org/repo1"], "events": ["release"]},
        ],
    }

    assert _sent_to(_run_event(mod, "push", _push_payload("org/repo1", "refs/heads/main"))) == {"#dev"}
    assert _sent_to(_run_event(mod, "release", _release_payload("org/repo1"))) == {"#release"}
    assert _run_event(mod, "issues", _issue_payload("org/repo1")).sent == []


def test_channel_without_events_uses_global_default() -> None:
    mod = GitHubModule()
    mod.config = {
        "events": ["issues"],
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"]},
            {"name": "#ops", "repos": ["org/repo1"], "events": ["push"]},
        ],
    }

    assert mod._channels_for("issues", "org/repo1") == ["#dev"]
    assert mod._channels_for("push", "org/repo1") == ["#ops"]


def test_builtin_default_events_when_none_configured() -> None:
    mod = GitHubModule()
    mod.config = {"channels": [{"name": "#dev", "repos": ["org/repo1"]}]}

    assert mod._enabled_events() == {"push", "issues", "pull_request"}


def test_wildcard_repos_match_every_repo() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#all", "repos": "*", "events": ["push"]},
            {"name": "#one", "repos": "org/repo1", "events": ["push"]},
        ],
    }

    assert mod._channels_for("push", "org/anything") == ["#all"]
    assert mod._channels_for("push", "org/repo1") == ["#all", "#one"]


def test_channel_with_no_repos_receives_nothing() -> None:
    mod = GitHubModule()
    mod.config = {"channels": [{"name": "#dev", "events": ["push"]}]}

    assert mod._channels_for("push", "org/repo1") == []


def test_enabled_events_is_union_of_channel_events() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"], "events": ["push"]},
            {"name": "#release", "repos": ["org/repo1"], "events": ["release", "workflow_run"]},
        ],
    }

    assert mod._enabled_events() == {"push", "release", "workflow_run"}


def test_duplicate_channel_entries_send_once() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"], "events": ["push"]},
            {"name": "#DEV", "repos": "*", "events": ["push", "issues"]},
        ],
    }

    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/main"))
    assert [ch for ch, _ in api.sent] == ["#dev"] * len(api.sent)
    assert mod._channels_for("issues", "org/other") == ["#DEV"]
    assert mod._configured_channels() == ["#dev"]


def test_repo_names_match_case_insensitively() -> None:
    mod = GitHubModule()
    mod.config = {"channels": [{"name": "#dev", "repos": ["Org/Repo1"], "events": ["push"]}]}

    assert mod._channels_for("push", "org/repo1") == ["#dev"]
    assert mod._channels_for("push", "ORG/REPO1") == ["#dev"]


def test_workflow_run_failure_reports_to_subscribed_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"], "events": ["workflow_run"]},
            {"name": "#ops", "repos": ["org/repo1"], "events": ["workflow_run"]},
        ],
    }

    api = _run_event(
        mod,
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

    assert _sent_to(api) == {"#dev", "#ops"}
    assert any("failed" in text for _, text in api.sent)


def test_push_event_debug_logs_repo_and_channels() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [
            {"name": "#dev", "repos": ["org/repo1"], "events": ["push"]},
            {"name": "#ops", "repos": ["org/repo1"], "events": ["push"]},
        ],
    }

    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/main"))

    log_lines = "\n".join(api.log.messages)
    assert "Received supported GitHub event push" in log_lines
    assert "org/repo1" in log_lines
    assert "#dev" in log_lines and "#ops" in log_lines


def test_legacy_channel_config_is_ignored_with_warning() -> None:
    mod = GitHubModule()
    mod.config = {
        "channel": "#default",
        "repos": [{"name": "org/repo1", "channels": ["#dev"]}],
    }
    api = FakeAPI()
    mod.api = api

    asyncio.run(mod.reload_config(mod.config))

    assert api.registered == []
    assert mod._channels_for("push", "org/repo1") == []
    assert any("no longer supported" in m for m in api.log.messages)


def test_reload_config_registers_new_channels() -> None:
    mod = GitHubModule()
    api = FakeAPI()
    mod.api = api

    asyncio.run(mod.reload_config({"channels": [{"name": "#dev", "repos": "*"}, {"name": "#ops", "repos": "*"}]}))

    assert api.registered == ["#dev", "#ops"]
    assert not any("no longer supported" in m for m in api.log.messages)


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
    mod.config = {"channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}]}

    assert mod._repo_branches_for("org/repo1") == []
    for ref in ("refs/heads/main", "refs/heads/feature/foo"):
        api = _run_event(mod, "push", _push_payload("org/repo1", ref))
        assert {ch for ch, _ in api.sent} == {"#dev"}


def test_repo_branches_filter_push_events() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}],
        "repos": [{"name": "org/repo1", "branches": ["main", "release/1.x"]}],
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
    mod.config = {
        "channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}],
        "repos": [{"name": "org/repo1", "branch": "main"}],
    }

    assert mod._repo_branches_for("org/repo1") == ["main"]
    api = _run_event(mod, "push", _push_payload("org/repo1", "refs/heads/dev"))
    assert api.sent == []


def test_repo_branches_do_not_filter_tags_issues_or_releases() -> None:
    mod = GitHubModule()
    mod.config = {
        "channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}],
        "repos": [{"name": "org/repo1", "branches": ["main"]}],
    }

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
    mod.config = {
        "channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}],
        "repos": [{"name": "org/repo1", "branches": ["main"]}],
    }

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
    mod.config = {
        "channels": [{"name": "#dev", "repos": ["org/repo1"], "events": ALL_EVENTS}],
        "repos": [{"name": "org/repo1", "branches": ["main"]}],
    }

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
        "channels": [
            {"name": "#dev", "repos": ["Org/Repo1"], "events": ALL_EVENTS},
            {"name": "#ops", "repos": ["org/repo2"], "events": ALL_EVENTS},
        ],
        "repos": [{"name": "Org/Repo1", "branches": ["main"]}],
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

    handler = make_handler(secret=secret, allowed_events=lambda: {"push"}, on_event=on_event)
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

    handler = make_handler(secret=secret, allowed_events=lambda: {"push"}, on_event=lambda *_args, **_kwargs: None)
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
