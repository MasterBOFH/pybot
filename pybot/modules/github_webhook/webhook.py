"""GitHub webhook HTTP handler with signature verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

log = logging.getLogger("pybot.modules.github_webhook")


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = signature_header.split("=", 1)[1]
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def make_handler(
    *,
    secret: str,
    allowed_events: set[str],
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    async def handle(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        sig = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(secret, body, sig):
            log.warning("Invalid GitHub webhook signature from %s", request.remote)
            return web.Response(status=401, text="invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return web.json_response({"ok": True, "pong": True})

        if allowed_events and event not in allowed_events:
            log.debug("Ignoring GitHub event %s", event)
            return web.json_response({"ok": True, "ignored": event})

        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return web.Response(status=400, text="invalid json")

        try:
            await on_event(event, payload)
        except Exception:
            log.exception("Error handling GitHub event %s", event)
            return web.Response(status=500, text="handler error")

        return web.json_response({"ok": True})

    return handle
