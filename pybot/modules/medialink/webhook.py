"""LiveKit webhook HTTP handlers (aiohttp) for medialink."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiohttp import web

log = logging.getLogger("pybot.modules.medialink.webhook")

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


def verify_livekit_webhook(
    *,
    api_key: str,
    api_secret: str,
    authorization: str | None,
    body: bytes,
) -> bool:
    """Verify LiveKit webhook JWT (Authorization header) against body SHA-256."""
    if not api_key or not api_secret or not authorization:
        return False
    try:
        import jwt
    except ImportError:
        log.error("PyJWT not installed — cannot verify LiveKit webhooks")
        return False
    try:
        decoded = jwt.decode(authorization, api_secret, algorithms=["HS256"])
    except Exception as exc:
        log.warning("LiveKit webhook JWT invalid: %s", exc)
        return False
    if decoded.get("iss") != api_key:
        log.warning("LiveKit webhook issuer mismatch")
        return False
    expected = decoded.get("sha256")
    if not expected:
        return False
    actual = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return hmac.compare_digest(expected, actual)


def make_webhook_handler(
    *,
    api_key: str,
    api_secret: str,
    require_auth: bool,
    on_event: EventHandler,
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    async def handler(request: web.Request) -> web.StreamResponse:
        import json

        body = await request.read()
        if require_auth:
            auth = request.headers.get("Authorization")
            if not verify_livekit_webhook(
                api_key=api_key,
                api_secret=api_secret,
                authorization=auth,
                body=body,
            ):
                return web.json_response({"error": "Invalid signature"}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return web.json_response({"error": "No JSON data"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Invalid payload"}, status=400)
        event = payload.get("event") or "unknown"
        try:
            await on_event(str(event), payload)
        except Exception:
            log.exception("Error processing LiveKit webhook %s", event)
            return web.json_response({"error": "Internal server error"}, status=500)
        return web.json_response({"status": "success"})

    return handler


def make_status_handler(
    get_rooms: Callable[[], list[dict[str, Any]]],
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    async def handler(_request: web.Request) -> web.StreamResponse:
        try:
            rooms = get_rooms()
            return web.json_response(
                {"active_rooms": len(rooms), "rooms": rooms}
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    return handler


def format_duration(room: dict[str, Any]) -> str:
    if "creation_time" not in room or "end_time" not in room:
        return ""
    try:
        start = datetime.fromisoformat(str(room["creation_time"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(room["end_time"]).replace("Z", "+00:00"))
        seconds = int((end - start).total_seconds())
        hours, rem = divmod(max(seconds, 0), 3600)
        minutes = rem // 60
        if hours:
            return f" | Duration: {hours}h {minutes}m"
        return f" | Duration: {minutes}m"
    except Exception:
        return ""
