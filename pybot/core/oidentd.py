"""oidentd user config rendering and file management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("pybot.core.oidentd")


def _quote_oidentd(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_user_config(
    *,
    reply: str,
    server_host: str | None = None,
    server_port: int | None = None,
) -> str:
    """Render minimal oidentd user configuration.

    Uses a global rule by default, or a server-scoped rule when host/port are set.
    """
    rule_parts: list[str] = []
    host = (server_host or "").strip()
    if host:
        rule_parts.extend(("to", host))
    if server_port is not None:
        rule_parts.extend(("fport", str(server_port)))

    selector = " ".join(rule_parts) if rule_parts else "global"
    lines = [
        "# Managed by pybot. Manual edits may be overwritten.",
        f"{selector} {{",
        f"    reply {_quote_oidentd(reply)}",
        "}",
        "",
    ]
    return "\n".join(lines)


def write_oidentd_user_config(irc_cfg: dict[str, Any]) -> Path | None:
    """Write oidentd user config when enabled.

    Returns written file path, or ``None`` when disabled / skipped.
    """
    oidentd_cfg = irc_cfg.get("oidentd") or {}
    if not isinstance(oidentd_cfg, dict) or not oidentd_cfg.get("enabled"):
        return None

    reply = str(oidentd_cfg.get("reply") or irc_cfg.get("username") or "").strip()
    if not reply:
        log.warning("oidentd enabled but no ident reply configured; skipping file write")
        return None

    path_value = str(oidentd_cfg.get("path") or "~/.config/oidentd.conf")
    path = Path(path_value).expanduser()

    port_raw = oidentd_cfg.get("server_port")
    server_port: int | None = None
    if port_raw not in (None, ""):
        try:
            server_port = int(port_raw)
        except (TypeError, ValueError):
            log.warning("oidentd server_port must be an integer, got %r", port_raw)
            return None

    content = render_user_config(
        reply=reply,
        server_host=oidentd_cfg.get("server_host"),
        server_port=server_port,
    )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o644)
    except OSError:
        log.exception("Failed to write oidentd user config at %s", path)
        return None

    log.info("Wrote oidentd user config at %s", path)
    return path
