"""Shared aiohttp application for module webhooks/APIs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

log = logging.getLogger("pybot.core.http")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class HttpServer:
    """HTTP server with owner-scoped dynamic routes (hot-reload friendly)."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self.app = web.Application()
        # (method, path) -> (owner, handler)
        self._routes: dict[tuple[str, str], tuple[str, Handler]] = {}
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._started = False
        self._catch_all_added = False

    def configure(self, host: str, port: int) -> bool:
        changed = (host, port) != (self.host, self.port)
        self.host = host
        self.port = port
        return changed and self._started

    def _ensure_dispatcher(self) -> None:
        if self._catch_all_added:
            return

        async def dispatch(request: web.Request) -> web.StreamResponse:
            key = (request.method.upper(), request.path)
            # also try without trailing slash variants
            entry = self._routes.get(key)
            if entry is None and request.path.endswith("/") and len(request.path) > 1:
                entry = self._routes.get((key[0], request.path.rstrip("/")))
            if entry is None and not request.path.endswith("/"):
                entry = self._routes.get((key[0], request.path + "/"))
            if entry is None:
                raise web.HTTPNotFound()
            _owner, handler = entry
            return await handler(request)

        # Register broad routes for common methods
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            self.app.router.add_route(method, "/{path:.*}", dispatch)
        self._catch_all_added = True

    def mount(
        self,
        method: str,
        path: str,
        handler: Handler,
        *,
        owner: str,
    ) -> None:
        self._ensure_dispatcher()
        method = method.upper()
        if not path.startswith("/"):
            path = "/" + path
        self._routes[(method, path)] = (owner, handler)
        log.info("Mounted %s %s for %s", method, path, owner)

    def unmount_owner(self, owner: str) -> None:
        remove = [k for k, (own, _) in self._routes.items() if own == owner]
        for k in remove:
            del self._routes[k]
        if remove:
            log.info("Unmounted %d route(s) for %s", len(remove), owner)

    async def start(self) -> None:
        if self._started:
            return
        self._ensure_dispatcher()
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._started = True
        log.info("HTTP server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        # Clear refs first so a concurrent stop (double SIGINT) is a no-op.
        runner = self._runner
        self._site = None
        self._runner = None
        self._started = False
        if runner is None:
            return
        try:
            # cleanup() stops sites; may race if two cleanups interleave on sleep(0)
            await runner.cleanup()
        except Exception:
            log.debug("HTTP runner cleanup ignored", exc_info=True)

    async def restart(self) -> None:
        await self.stop()
        # New Application; re-add dispatcher, keep route table
        self.app = web.Application()
        self._catch_all_added = False
        self._ensure_dispatcher()
        await self.start()
