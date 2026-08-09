"""Bot orchestrator: IRC, HTTP, modules, reload, admin commands."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import signal
from pathlib import Path
from typing import Any

from pybot.config import load_config, module_config
from pybot.core.api import BotAPI
from pybot.core.events import EventBus
from pybot.core.http import HttpServer
from pybot.core.module import Module, collect_handlers, load_module_class
from pybot.core.reload import reload_module_by_name, reload_package_modules
from pybot.core.timers import TimerEngine
from pybot.irc.client import IRCClient
from pybot.logging_setup import RawLogger, setup_logging

log = logging.getLogger("pybot.core.bot")


class Bot:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.raw: RawLogger = setup_logging(self.config.get("logging"))
        self.bus = EventBus()
        self.timers = TimerEngine()
        http_cfg = self.config.get("http") or {}
        self.http = HttpServer(
            host=http_cfg.get("host", "0.0.0.0"),
            port=int(http_cfg.get("port", 8080)),
        )
        self.irc = IRCClient(
            self.config["irc"],
            emit=self._emit,
            timers=self.timers,
            raw=self.raw,
        )
        self.modules: dict[str, Module] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = asyncio.Event()
        self._stopping = False
        self._reconnecting = False
        self._reconnect_delay = self._reconnect_initial()
        self._reconnect_timer = None

    def module_config(self, name: str) -> dict[str, Any]:
        return module_config(self.config, name)

    def _reconnect_cfg(self) -> dict[str, Any]:
        return (self.config.get("irc") or {}).get("reconnect") or {}

    def _reconnect_initial(self) -> float:
        cfg = self._reconnect_cfg()
        return float(cfg.get("initial_delay", 10))

    def _reconnect_step(self) -> float:
        return float(self._reconnect_cfg().get("step", 10))

    def _reconnect_max(self) -> float:
        return float(self._reconnect_cfg().get("max_delay", 60))

    def _reconnect_enabled(self) -> bool:
        return bool(self._reconnect_cfg().get("enabled", True))

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if event == "disconnect":
            await self._on_irc_disconnect(payload)
        elif event == "registered":
            self._on_irc_registered()
        # Admin commands on privmsg before module handlers
        if event == "privmsg":
            await self._maybe_admin(payload)
        await self.bus.emit(event, payload)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        loop = self.loop
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.stop(f"signal {s.name}")))
            except NotImplementedError:
                pass
        try:
            loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.create_task(self.reload_config_and_modules()))
        except (NotImplementedError, AttributeError):
            pass

        await self.http.start()
        await self.load_modules()
        try:
            await self.irc.connect()
        except Exception:
            log.exception("Initial IRC connect failed")
            self._schedule_reconnect()
        log.info("Bot started")
        await self._stop_event.wait()

    async def stop(self, reason: str = "shutdown") -> None:
        if self._stopping:
            return
        self._stopping = True
        log.info("Stopping: %s", reason)
        self._cancel_reconnect_timer()
        try:
            await self.unload_modules()
            self.timers.cancel_all()
            await self.irc.disconnect(reason)
            await self.http.stop()
        except Exception:
            log.exception("Error during shutdown")
        finally:
            self._stop_event.set()

    async def load_modules(self) -> None:
        mods_cfg = self.config.get("modules") or {}
        for name, cfg in mods_cfg.items():
            if not isinstance(cfg, dict) or not cfg.get("enabled", True):
                continue
            try:
                await self.load_module(name)
            except Exception:
                log.exception("Failed to load module %s", name)

    async def load_module(
        self, name: str, *, state: dict[str, Any] | None = None
    ) -> None:
        if name in self.modules:
            await self.unload_module(name)
        cls = load_module_class(name)
        instance = cls()
        if not getattr(instance, "name", None) or instance.name == "unnamed":
            instance.name = name
        if state:
            instance.load_state(state)
        api = BotAPI(self, name)
        await instance.setup(api)
        for event, handler in collect_handlers(instance).items():
            self.bus.on(event, handler, owner=api.owner)
        self.modules[name] = instance
        log.info("Loaded module %s", name)

    async def unload_module(self, name: str) -> None:
        instance = self.modules.pop(name, None)
        if not instance:
            return
        owner = f"module:{name}"
        self.timers.cancel_owner(owner)
        self.bus.off_owner(owner)
        self.http.unmount_owner(owner)
        try:
            await instance.teardown()
        except Exception:
            log.exception("Teardown failed for %s", name)
        log.info("Unloaded module %s", name)

    async def unload_modules(self) -> None:
        for name in list(self.modules):
            await self.unload_module(name)

    async def reload_module(self, name: str) -> None:
        log.info("Reloading module %s", name)
        old = self.modules.get(name)
        state = old.get_state() if old else {}
        await self.unload_module(name)
        reload_module_by_name(name)
        cfg = self.module_config(name)
        if cfg.get("enabled", True):
            await self.load_module(name, state=state)

    async def reload_modules(self) -> None:
        for name in list(self.modules):
            await self.reload_module(name)
        # load newly enabled
        mods_cfg = self.config.get("modules") or {}
        for name, cfg in mods_cfg.items():
            if name not in self.modules and isinstance(cfg, dict) and cfg.get("enabled", True):
                await self.load_module(name)

    async def reload_config_and_modules(self) -> None:
        log.info("Reloading config + modules (SIGHUP)")
        self.config = load_config(self.config_path)
        setup_logging(self.config.get("logging"))
        irc_cfg = self.config.get("irc") or {}
        self.irc.config = irc_cfg
        flood = irc_cfg.get("flood") or {}
        self.irc.configure_flood(
            burst=flood.get("burst"),
            rate=flood.get("rate"),
        )
        await self.irc.sync_channels(irc_cfg.get("channels") or [])
        http_cfg = self.config.get("http") or {}
        need_http_restart = self.http.configure(
            http_cfg.get("host", "0.0.0.0"),
            int(http_cfg.get("port", 8080)),
        )
        if need_http_restart:
            await self.http.restart()
        await self.reload_modules()

    async def reload_core(self) -> None:
        """Reload non-sticky core/irc code; keep connection and state."""
        log.info("Reloading core logic (sticky state preserved)")
        reload_package_modules("pybot.irc")
        reload_package_modules("pybot.core")
        # Re-bind emit/timers on client if needed — instances persist
        log.info("Core reload complete")

    async def reconnect(self) -> None:
        """Immediate reconnect (admin / internal). Resets backoff on success."""
        self._cancel_reconnect_timer()
        await self._reconnect_now(reason="manual")

    async def _on_irc_disconnect(self, payload: dict[str, Any]) -> None:
        err = payload.get("error")
        log.warning("IRC disconnected%s", f": {err}" if err else "")
        if self._stopping or self._reconnecting:
            return
        self._schedule_reconnect()

    def _on_irc_registered(self) -> None:
        self._reconnect_delay = self._reconnect_initial()
        self._cancel_reconnect_timer()
        log.debug("Reconnect backoff reset to %ss", self._reconnect_delay)

    def _cancel_reconnect_timer(self) -> None:
        if self._reconnect_timer is not None:
            self.timers.cancel(self._reconnect_timer)
            self._reconnect_timer = None

    def _schedule_reconnect(self) -> None:
        if self._stopping or not self._reconnect_enabled():
            return
        if self._reconnect_timer is not None:
            return
        delay = self._reconnect_delay
        log.warning("Scheduling IRC reconnect in %.0fs", delay)
        self._reconnect_timer = self.timers.after(
            delay,
            self._auto_reconnect_attempt,
            name="irc_reconnect",
            owner="core:reconnect",
        )
        self._reconnect_delay = min(
            delay + self._reconnect_step(),
            self._reconnect_max(),
        )

    async def _auto_reconnect_attempt(self) -> None:
        self._reconnect_timer = None
        if self._stopping:
            return
        try:
            await self._reconnect_now(reason="auto")
        except Exception:
            log.exception("IRC reconnect attempt failed")
            self._schedule_reconnect()

    async def _reconnect_now(self, *, reason: str) -> None:
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            log.info("Reconnecting IRC (%s)…", reason)
            try:
                await self.irc.disconnect("Reconnecting")
            except Exception:
                log.debug("Disconnect during reconnect ignored", exc_info=True)
            # Fresh client — new connection + state journal
            self.irc = IRCClient(
                self.config["irc"],
                emit=self._emit,
                timers=self.timers,
                raw=self.raw,
            )
            await self.irc.connect()
        except Exception:
            # Caller (auto) may reschedule; manual raises after schedule
            if reason == "manual":
                self._schedule_reconnect()
            raise
        finally:
            self._reconnecting = False

    def _is_admin(self, payload: dict[str, Any]) -> bool:
        admin = (self.config.get("irc") or {}).get("admin") or {}
        nick = payload.get("nick") or ""
        user = payload.get("user")
        host = payload.get("host")
        account = payload.get("account")
        journal = self.irc.state.get_user(nick) if nick else None
        if journal:
            user = user or journal.user
            host = host or journal.host
            if not account:
                account = journal.account

        for mask in admin.get("hosts") or []:
            if mask and self._hostmask_match(str(mask), nick, user, host):
                return True

        accounts = {a.lower() for a in admin.get("accounts") or []}
        if accounts and account and account.lower() in accounts:
            return True
        return False

    def _hostmask_match(
        self,
        mask: str,
        nick: str,
        user: str | None,
        host: str | None,
    ) -> bool:
        """Match nick!user@host against an IRC hostmask pattern (* and ?)."""
        mask = mask.strip()
        if not mask:
            return False
        # Allow shorthand: user@host or bare host
        if "!" not in mask:
            if "@" in mask:
                mask = f"*!{mask}"
            else:
                mask = f"*!*@{mask}"
        identity = (
            f"{self.irc.isupport.casefold(nick)}!"
            f"{(user or '').lower()}@{(host or '').lower()}"
        )
        # Casefold nick portion of the pattern; lower the rest
        if "!" in mask:
            nick_pat, rest = mask.split("!", 1)
            pattern = f"{self.irc.isupport.casefold(nick_pat)}!{rest.lower()}"
        else:
            pattern = mask.lower()
        return fnmatch.fnmatchcase(identity, pattern)

    async def _maybe_admin(self, payload: dict[str, Any]) -> None:
        text = payload.get("text") or ""
        admin = (self.config.get("irc") or {}).get("admin") or {}
        prefix = admin.get("prefix") or "~"
        if not text.startswith(prefix):
            return
        if not self._is_admin(payload):
            return
        # Only respond to messages to us or channels (both ok)
        body = text[len(prefix) :].strip()
        parts = body.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        nick = payload.get("nick")
        target = payload.get("target")
        # reply to channel or nick
        reply_to = target if target and target[:1] in self.irc.isupport.chantypes else nick

        async def reply(msg: str) -> None:
            if reply_to:
                await self.irc.privmsg(reply_to, msg)

        try:
            if cmd == "reload":
                if not args:
                    await reply("usage: reload modules|module <name>|config|core")
                    return
                what = args[0].lower()
                if what == "modules":
                    await self.reload_modules()
                    await reply("reloaded modules")
                elif what == "module" and len(args) >= 2:
                    await self.reload_module(args[1])
                    await reply(f"reloaded module {args[1]}")
                elif what == "config":
                    await self.reload_config_and_modules()
                    await reply("reloaded config + modules")
                elif what == "core":
                    await self.reload_core()
                    await reply("reloaded core")
                else:
                    await reply("usage: reload modules|module <name>|config|core")
            elif cmd == "reconnect":
                await reply("reconnecting…")
                await self.reconnect()
            elif cmd == "modules":
                await reply("modules: " + (", ".join(self.modules) or "(none)"))
        except Exception as exc:
            log.exception("Admin command failed")
            await reply(f"error: {exc}")
