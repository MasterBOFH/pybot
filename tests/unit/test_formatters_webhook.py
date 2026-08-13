"""Unit tests for GitHub formatters and signature verify."""

from __future__ import annotations

import hashlib
import hmac

from pybot.modules.github.formatters import (
    format_issues,
    format_pull_request,
    format_push,
    format_release,
)
from pybot.modules.github.webhook import verify_signature


def test_format_push() -> None:
    lines = format_push(
        {
            "ref": "refs/heads/main",
            "pusher": {"name": "dev"},
            "repository": {"full_name": "org/repo"},
            "commits": [
                {
                    "id": "abcdef123",
                    "author": {"name": "dev"},
                    "message": "fix stuff",
                }
            ],
        }
    )
    assert "pushed" in lines[0]
    assert "\x02dev\x02" in lines[0]
    assert "\x02abcdef1\x02" in lines[1]
    assert "📦" in lines[0]

    plain = format_push(
        {
            "ref": "refs/heads/main",
            "pusher": {"name": "dev"},
            "repository": {"full_name": "org/repo"},
            "commits": [],
        },
        emojis=False,
    )
    assert "📦" not in plain[0]


def test_format_tag_push() -> None:
    lines = format_push(
        {
            "ref": "refs/tags/v0.1.1",
            "after": "709f0dbc0f6d",
            "pusher": {"name": "MrIron-no"},
            "repository": {
                "full_name": "MasterBOFH/GoBNC",
                "html_url": "https://github.com/MasterBOFH/GoBNC",
            },
            "commits": [],
        }
    )
    assert "pushed tag" in lines[0]
    assert "v0.1.1" in lines[0]
    assert "0 commits" not in lines[0]
    assert "🏷️" in lines[0]
    assert lines[1].endswith("/releases/tag/v0.1.1")


def test_format_release() -> None:
    lines = format_release(
        {
            "action": "published",
            "sender": {"login": "MrIron-no"},
            "repository": {"full_name": "MasterBOFH/GoBNC"},
            "release": {
                "tag_name": "v0.1.1",
                "name": "v0.1.1",
                "html_url": "https://github.com/MasterBOFH/GoBNC/releases/tag/v0.1.1",
                "draft": False,
                "prerelease": False,
            },
        }
    )
    assert "published release" in lines[0]
    assert "v0.1.1" in lines[0]
    assert "🚀" in lines[0]


def test_format_issue_and_pr() -> None:
    issue_lines = format_issues(
        {
            "action": "opened",
            "sender": {"login": "alice"},
            "repository": {"full_name": "org/repo"},
            "issue": {
                "number": 7,
                "title": "bug",
                "html_url": "https://example/i/7",
            },
        }
    )
    assert "#7" in issue_lines[0]
    assert "\x02#7\x02" in issue_lines[0]

    pr_lines = format_pull_request(
        {
            "action": "closed",
            "sender": {"login": "bob"},
            "repository": {"full_name": "org/repo"},
            "pull_request": {
                "number": 3,
                "title": "feat",
                "merged": True,
                "html_url": "https://example/p/3",
                "base": {"ref": "main"},
                "head": {"ref": "feat"},
            },
        }
    )
    assert "merged" in pr_lines[0]
    assert "💜" in pr_lines[0]


def test_webhook_signature() -> None:
    body = b'{"zen":"x"}'
    secret = "test"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(secret, body, sig)
    assert not verify_signature(secret, body, "sha256=deadbeef")
