"""Format GitHub webhook payloads as IRC lines (bold + optional emoji)."""

from __future__ import annotations

from typing import Any

# mIRC / IRC formatting
BOLD = "\x02"
ITALIC = "\x1d"
RESET = "\x0f"


def bold(text: str) -> str:
    return f"{BOLD}{text}{BOLD}"


def _truncate(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _emoji(enabled: bool, symbol: str) -> str:
    return f"{symbol} " if enabled else ""


_ISSUE_EMOJI = {
    "opened": "🟩",
    "closed": "✅",
    "reopened": "🔄",
    "edited": "✏️",
    "deleted": "🗑️",
    "assigned": "👤",
    "labeled": "🏷️",
}

_PR_EMOJI = {
    "opened": "🔀",
    "closed": "❌",
    "merged": "💜",
    "reopened": "🔄",
    "ready_for_review": "👀",
    "converted_to_draft": "📝",
    "edited": "✏️",
}


def format_push(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    ref = payload.get("ref") or ""
    if ref.startswith("refs/tags/"):
        return _format_tag_push(payload, emojis=emojis)

    repo = (payload.get("repository") or {}).get("full_name", "?")
    pusher = (payload.get("pusher") or {}).get("name") or (
        (payload.get("sender") or {}).get("login", "?")
    )
    branch = ref.rsplit("/", 1)[-1] if ref else "?"
    commits = payload.get("commits") or []
    forced = payload.get("forced")
    deleted = payload.get("deleted")
    n = len(commits)
    commit_word = "commit" if n == 1 else "commits"
    force_bit = f" {bold('forced')}" if forced else ""
    icon = _emoji(emojis, "⚠️" if forced else "📦")

    if deleted:
        return [
            f"{_emoji(emojis, '🗑️')}{bold('GitHub')} {bold(pusher)} deleted branch "
            f"{bold(branch)} on {bold(repo)}"
        ]

    lines = [
        f"{icon}{bold('GitHub')} {bold(pusher)} pushed {bold(str(n))} {commit_word} "
        f"to {bold(repo)}:{bold(branch)}{force_bit}"
    ]
    for c in commits[:5]:
        sha = (c.get("id") or "")[:7]
        author = (c.get("author") or {}).get("name") or "?"
        msg = _truncate(c.get("message") or "", 120)
        lines.append(f"  {bold(sha)} {author}: {msg}")
    if n > 5:
        lines.append(f"  … and {n - 5} more")
    compare = payload.get("compare")
    if compare:
        lines.append(f"  {compare}")
    return lines


def _format_tag_push(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    """Tag pushes are push events with refs/tags/* and usually 0 commits."""
    repo = (payload.get("repository") or {}).get("full_name", "?")
    pusher = (payload.get("pusher") or {}).get("name") or (
        (payload.get("sender") or {}).get("login", "?")
    )
    ref = payload.get("ref") or ""
    tag = ref.rsplit("/", 1)[-1] if ref else "?"
    repo_url = (payload.get("repository") or {}).get("html_url") or ""

    if payload.get("deleted"):
        return [
            f"{_emoji(emojis, '🗑️')}{bold('GitHub')} {bold(pusher)} deleted tag "
            f"{bold(tag)} on {bold(repo)}"
        ]

    sha = (payload.get("after") or "")[:7]
    lines = [
        f"{_emoji(emojis, '🏷️')}{bold('GitHub')} {bold(pusher)} pushed tag "
        f"{bold(tag)} to {bold(repo)}"
        + (f" ({bold(sha)})" if sha and sha != "0000000" else "")
    ]
    if repo_url:
        lines.append(f"  {repo_url}/releases/tag/{tag}")
    return lines


def format_release(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    """GitHub Release published/edited/deleted (distinct from a bare tag push)."""
    action = payload.get("action", "?")
    interesting = {"published", "released", "prereleased", "created", "edited", "deleted"}
    if action not in interesting:
        return []
    # "created" for draft releases is noisy; only announce when published-ish
    if action == "created" and (payload.get("release") or {}).get("draft"):
        return []

    release = payload.get("release") or {}
    repo = (payload.get("repository") or {}).get("full_name", "?")
    user = (payload.get("sender") or {}).get("login", "?")
    tag = release.get("tag_name") or "?"
    name = _truncate(release.get("name") or tag, 100)
    url = release.get("html_url") or ""
    prerelease = bool(release.get("prerelease"))

    if action == "published" or action == "released":
        verb = "published prerelease" if prerelease else "published release"
        icon = "🚀"
    elif action == "prereleased":
        verb = "published prerelease"
        icon = "🚀"
    elif action == "deleted":
        verb = "deleted release"
        icon = "🗑️"
    else:
        verb = f"{action} release"
        icon = "🏷️"

    lines = [
        f"{_emoji(emojis, icon)}{bold('GitHub')} {bold(user)} {verb} "
        f"{bold(name)} ({bold(tag)}) in {bold(repo)}"
    ]
    if url:
        lines.append(f"  {url}")
    return lines


def format_issues(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    action = payload.get("action", "?")
    issue = payload.get("issue") or {}
    repo = (payload.get("repository") or {}).get("full_name", "?")
    user = (payload.get("sender") or {}).get("login", "?")
    number = issue.get("number", "?")
    title = _truncate(issue.get("title") or "", 100)
    url = issue.get("html_url") or ""
    icon = _emoji(emojis, _ISSUE_EMOJI.get(action, "🎫"))
    lines = [
        f"{icon}{bold('GitHub')} {bold(user)} {action} issue "
        f"{bold(f'#{number}')} in {bold(repo)}: {title}"
    ]
    if url:
        lines.append(f"  {url}")
    return lines


def format_pull_request(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    action = payload.get("action", "?")
    interesting = {
        "opened",
        "closed",
        "reopened",
        "ready_for_review",
        "converted_to_draft",
        "edited",
    }
    if action not in interesting:
        return []
    pr = payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name", "?")
    user = (payload.get("sender") or {}).get("login", "?")
    number = pr.get("number", "?")
    title = _truncate(pr.get("title") or "", 100)
    url = pr.get("html_url") or ""
    if action == "closed" and pr.get("merged"):
        action = "merged"
    extra = ""
    base = (pr.get("base") or {}).get("ref")
    head = (pr.get("head") or {}).get("ref")
    if base and head:
        extra = f" ({bold(head)} → {bold(base)})"
    icon = _emoji(emojis, _PR_EMOJI.get(action, "🔀"))
    lines = [
        f"{icon}{bold('GitHub')} {bold(user)} {bold(action)} PR "
        f"{bold(f'#{number}')} in {bold(repo)}: {title}{extra}"
    ]
    if url:
        lines.append(f"  {url}")
    return lines


_WORKFLOW_CONCLUSION = {
    "success": ("passed", "✅"),
    "failure": ("failed", "❌"),
    "cancelled": ("was cancelled", "🚫"),
    "timed_out": ("timed out", "⏱️"),
    "action_required": ("requires action", "⚠️"),
}


def format_workflow_run(payload: dict[str, Any], *, emojis: bool = True) -> list[str]:
    """GitHub Actions workflow run completed (success/failure/etc.)."""
    if payload.get("action") != "completed":
        return []
    run = payload.get("workflow_run") or {}
    conclusion = run.get("conclusion") or ""
    verb_icon = _WORKFLOW_CONCLUSION.get(conclusion)
    if not verb_icon:
        return []
    verb, icon = verb_icon

    repo = (payload.get("repository") or {}).get("full_name", "?")
    name = run.get("name") or "workflow"
    branch = run.get("head_branch") or "?"
    number = run.get("run_number", "?")
    url = run.get("html_url") or ""

    lines = [
        f"{_emoji(emojis, icon)}{bold('GitHub')} {bold(name)} #{number} {verb} "
        f"on {bold(branch)} in {bold(repo)}"
    ]
    if url:
        lines.append(f"  {url}")
    return lines


FORMATTERS = {
    "push": format_push,
    "issues": format_issues,
    "pull_request": format_pull_request,
    "release": format_release,
    "workflow_run": format_workflow_run,
}


def format_event(
    event: str,
    payload: dict[str, Any],
    *,
    emojis: bool = True,
) -> list[str]:
    fn = FORMATTERS.get(event)
    if not fn:
        return []
    return fn(payload, emojis=emojis)
