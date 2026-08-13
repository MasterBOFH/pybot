"""MediaLink — LiveKit rooms, join tokens, and webhook events."""

from __future__ import annotations

import secrets
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module, on
from pybot.modules.medialink.livekit_api import LiveKitAPI
from pybot.modules.medialink.webhook import (
    format_duration,
    make_status_handler,
    make_webhook_handler,
)


class MedialinkModule(Module):
    name = "medialink"

    def __init__(self) -> None:
        super().__init__()
        self.lk: LiveKitAPI | None = None
        self._pending_state: dict[str, Any] = {}
        # (channel name, debug announcements enabled)
        self._channels: list[tuple[str, bool]] = []
        self._room_channels: set[str] = set()  # casefolded names allowed for join
        self._cmd_prefix = "$"
        self._token_url = ""
        self._shortener_mode = "none"
        self._shortener_timeout = 4.0
        self._shortener_tinyurl_endpoint = "https://tinyurl.com/api-create.php"
        self._shortener_isgd_endpoint = "https://is.gd/create.php"
        self._shortener_local_base = ""
        self._shortener_local_path = "/j"
        self._shortlinks: dict[str, dict[str, Any]] = {}
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}

    def load_state(self, state: dict[str, Any]) -> None:
        self._pending_state = state or {}

    def get_state(self) -> dict[str, Any]:
        sessions = {
            f"{ident}\0{room}": info for (ident, room), info in self.sessions.items()
        }
        room_cache = self.lk.room_cache.copy() if self.lk else {}
        # datetime objects → iso for safety
        for data in room_cache.values():
            last = data.get("last_seen")
            if hasattr(last, "isoformat"):
                data["last_seen"] = last
        return {
            "sessions": sessions,
            "room_cache": room_cache,
            "participant_cache": (
                self.lk.participant_cache.copy() if self.lk else {}
            ),
            "shortlinks": self._shortlinks.copy(),
        }

    def _apply_state(self, state: dict[str, Any]) -> None:
        raw = state.get("sessions") or {}
        self.sessions = {}
        for key, info in raw.items():
            if "\0" in key:
                ident, room = key.split("\0", 1)
                self.sessions[(ident, room)] = dict(info)
        if self.lk:
            self.lk.room_cache = (state.get("room_cache") or {}).copy()
            self.lk.participant_cache = (state.get("participant_cache") or {}).copy()
        self._shortlinks = (state.get("shortlinks") or {}).copy()

    def _parse_channels(self, cfg: dict[str, Any]) -> list[tuple[str, bool]]:
        """Return (channel, debug). debug defaults to false when omitted."""
        channels_cfg = cfg.get("channels")
        if isinstance(channels_cfg, list) and channels_cfg:
            out: list[tuple[str, bool]] = []
            for entry in channels_cfg:
                if isinstance(entry, str):
                    out.append((entry, False))
                    continue
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name") or entry.get("channel")
                if not name:
                    continue
                out.append((str(name), bool(entry.get("debug", False))))
            return out
        single = cfg.get("channel")
        if single:
            return [(str(single), False)]
        return []

    def _room_allowed(self, room_name: str) -> bool:
        assert self.api is not None
        return self.api.casefold(room_name) in self._room_channels

    async def _announce(self, event_type: str, text: str) -> None:
        assert self.api is not None
        et = event_type.lower()
        for channel, debug in self._channels:
            if et == "debug" and not debug:
                continue
            if et in ("info", "debug"):
                try:
                    await self.api.privmsg(channel, text)
                except RuntimeError as exc:
                    if "Not connected" in str(exc):
                        self.api.log.warning(
                            "medialink: skipping IRC announcement to %s while disconnected",
                            channel,
                        )
                        return
                    raise

    async def _announce_room(self, room_name: str, text: str) -> None:
        """Send to the IRC channel matching the LiveKit room name, if configured."""
        assert self.api is not None
        if not self._room_allowed(room_name):
            return
        try:
            # Prefer configured casing
            for channel, _debug in self._channels:
                if self.api.nicks_equal(channel, room_name):
                    await self.api.privmsg(channel, text)
                    return
            await self.api.privmsg(room_name, text)
        except RuntimeError as exc:
            if "Not connected" in str(exc):
                self.api.log.warning(
                    "medialink: skipping room announcement to %s while disconnected",
                    room_name,
                )
                return
            raise

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        cfg = self.config
        bot_cfg = api.get_bot_config()
        admin = (bot_cfg.get("irc") or {}).get("admin") or {}
        self._cmd_prefix = (
            cfg.get("command_prefix") or admin.get("prefix") or "$"
        )
        self._channels = self._parse_channels(cfg)
        self._room_channels = {api.casefold(name) for name, _ in self._channels}
        self._token_url = cfg.get("token_url") or "https://example.com/?token="
        shortener = cfg.get("shortener") or {}
        self._shortener_mode = str(shortener.get("mode") or "none").strip().lower()
        self._shortener_timeout = float(shortener.get("timeout_seconds") or 4.0)
        self._shortener_tinyurl_endpoint = str(
            shortener.get("tinyurl_endpoint") or "https://tinyurl.com/api-create.php"
        )
        self._shortener_isgd_endpoint = str(
            shortener.get("isgd_endpoint") or "https://is.gd/create.php"
        )
        self._shortener_local_base = str(shortener.get("base_url") or "").rstrip("/")
        self._shortener_local_path = str(shortener.get("path") or "/j")
        if not self._shortener_local_path.startswith("/"):
            self._shortener_local_path = f"/{self._shortener_local_path}"
        if self._shortener_mode not in ("none", "tinyurl", "isgd", "local"):
            self.api.log.warning(
                "medialink: invalid shortener.mode '%s', disabling shortener",
                self._shortener_mode,
            )
            self._shortener_mode = "none"

        api_key = cfg.get("api_key") or ""
        api_secret = cfg.get("api_secret") or ""
        server_url = cfg.get("server_url") or ""
        if not api_key or not api_secret or not server_url:
            self.api.log.error(
                "medialink: api_key, api_secret, and server_url are required"
            )
            return

        self.lk = LiveKitAPI(
            api_key=api_key,
            api_secret=api_secret,
            server_url=server_url,
            token_ttl_minutes=int(cfg.get("token_ttl_minutes") or 60),
            announcements=cfg.get("announcements") or {},
            command_prefix=self._cmd_prefix,
            announce=self._announce,
            announce_room=self._announce_room,
        )
        if self._pending_state:
            self._apply_state(self._pending_state)
            self._pending_state = {}

        try:
            await self.lk.connect()
        except Exception:
            self.api.log.exception("medialink: LiveKit connect failed")

        poll_seconds = float(cfg.get("poll_seconds") or 30)
        self.api.every(poll_seconds, self._poll_tick, name="lk_poll", immediate=True)

        ttl = int(cfg.get("token_ttl_minutes") or 60)
        self.api.every(
            max(ttl * 60, 60),
            self._cleanup_sessions,
            name="lk_session_cleanup",
        )

        webhook = cfg.get("webhook") or {}
        if webhook.get("enabled", True):
            path = webhook.get("path") or "/livekit/webhook"
            require_auth = bool(webhook.get("verify", True))
            self.api.mount_route(
                "POST",
                path,
                make_webhook_handler(
                    api_key=api_key,
                    api_secret=api_secret,
                    require_auth=require_auth,
                    on_event=self._on_webhook_event,
                ),
            )
            self.api.mount_route(
                "GET",
                "/livekit/status",
                make_status_handler(lambda: self.lk.get_active_rooms() if self.lk else []),
            )
            self.api.log.info(
                "LiveKit webhook POST %s (verify=%s)", path, require_auth
            )

        if self._shortener_mode == "local":
            self.api.mount_route("GET", self._shortener_local_path, self._shortlink_handler)
            if not self._shortener_local_base:
                self.api.log.warning(
                    "medialink: shortener.mode=local but shortener.base_url is empty; "
                    "falling back to full token URLs"
                )

        dest = ", ".join(
            f"{ch}{'[debug]' if dbg else ''}" for ch, dbg in self._channels
        ) or "(no channels)"
        self.api.log.info(
            "MediaLink ready → rooms/channels %s (prefix %s)",
            dest,
            self._cmd_prefix,
        )

    async def _shortlink_handler(self, request):
        from aiohttp import web

        code = (request.query.get("c") or "").strip()
        entry = self._shortlinks.get(code)
        now = int(time.time())
        if not entry:
            return web.Response(status=404, text="Unknown short link")
        if int(entry.get("expires_at") or 0) <= now:
            self._shortlinks.pop(code, None)
            return web.Response(status=410, text="Short link expired")
        location = str(entry.get("url") or "")
        if not location:
            return web.Response(status=404, text="Invalid short link")
        raise web.HTTPFound(location=location)

    def _cleanup_shortlinks(self) -> None:
        now = int(time.time())
        stale = [
            code
            for code, entry in self._shortlinks.items()
            if int(entry.get("expires_at") or 0) <= now
        ]
        for code in stale:
            self._shortlinks.pop(code, None)

    async def _shorten_join_url(self, url: str) -> str:
        if self._shortener_mode == "none":
            return url
        if self._shortener_mode == "tinyurl":
            return await self._shorten_tinyurl(url)
        if self._shortener_mode == "isgd":
            return await self._shorten_isgd(url)
        if self._shortener_mode == "local":
            return self._shorten_local(url)
        return url

    async def _shorten_tinyurl(self, url: str) -> str:
        assert self.api is not None
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=max(self._shortener_timeout, 1.0))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                req_url = self._tinyurl_request_url(url)
                async with session.get(
                    req_url,
                ) as resp:
                    if resp.status != 200:
                        self.api.log.warning(
                            "medialink: tinyurl shorten failed status=%s", resp.status
                        )
                        return url
                    out = (await resp.text()).strip()
                    if out.startswith("http://") or out.startswith("https://"):
                        return out
                    self.api.log.warning("medialink: tinyurl returned invalid response")
                    return url
        except Exception:
            self.api.log.debug("medialink: tinyurl shortening failed", exc_info=True)
            return url

    def _tinyurl_request_url(self, url: str) -> str:
        """Build TinyURL API request preserving nested query '=' semantics.

        TinyURL currently mangles fully percent-encoded nested query separators
        (`token%3D...`). Keep URL delimiters readable while still encoding unsafe
        characters like spaces.
        """
        sep = "&" if "?" in self._shortener_tinyurl_endpoint else "?"
        encoded_target = quote(url, safe=":/?=%#")
        return f"{self._shortener_tinyurl_endpoint}{sep}url={encoded_target}"

    async def _shorten_isgd(self, url: str) -> str:
        assert self.api is not None
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=max(self._shortener_timeout, 1.0))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    self._shortener_isgd_endpoint,
                    params={"format": "simple", "url": url},
                ) as resp:
                    if resp.status != 200:
                        self.api.log.warning(
                            "medialink: is.gd shorten failed status=%s", resp.status
                        )
                        return url
                    out = (await resp.text()).strip()
                    if out.startswith("http://") or out.startswith("https://"):
                        return out
                    self.api.log.warning("medialink: is.gd returned invalid response")
                    return url
        except Exception:
            self.api.log.debug("medialink: is.gd shortening failed", exc_info=True)
            return url

    def _shorten_local(self, url: str) -> str:
        assert self.api is not None
        if not self._shortener_local_base:
            return url
        self._cleanup_shortlinks()
        code = self._new_short_code()
        ttl = int(self.config.get("token_ttl_minutes") or 60)
        self._shortlinks[code] = {
            "url": url,
            "expires_at": int(time.time()) + max(ttl, 1) * 60,
        }
        q = urlencode({"c": code})
        return f"{self._shortener_local_base}{self._shortener_local_path}?{q}"

    def _new_short_code(self) -> str:
        # 6 random bytes => ~8 char URL-safe token.
        for _ in range(8):
            code = secrets.token_urlsafe(6).rstrip("=")
            if code not in self._shortlinks:
                return code
        # Extremely unlikely fallback.
        return secrets.token_urlsafe(8).rstrip("=")

    def _build_join_url(self, token: str) -> str:
        """Build a safe join URL from token_url config and token.

        Supported formats:
        - Legacy suffix: https://frontend/?token=
        - Template: https://frontend/?token={token}
        """
        base = self._token_url
        if "{token}" in base:
            return base.replace("{token}", quote(token, safe=""))

        parts = urlsplit(base)
        if parts.scheme and parts.netloc and parts.query:
            items = parse_qsl(parts.query, keep_blank_values=True)
            for idx, (key, value) in enumerate(items):
                if value == "":
                    items[idx] = (key, token)
                    new_query = urlencode(items, doseq=True)
                    return urlunsplit(
                        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
                    )

        # Backward-compatible fallback: append encoded token to configured prefix.
        return f"{base}{quote(token, safe='')}"

    async def teardown(self) -> None:
        if self.api:
            self.api.unmount_routes()
        if self.lk:
            await self.lk.close()
            self.lk = None
        await super().teardown()

    async def _poll_tick(self) -> None:
        if self.lk:
            await self.lk.poll_rooms()

    async def _cleanup_sessions(self) -> None:
        ttl = int(self.config.get("token_ttl_minutes") or 60)
        cutoff = int(time.time()) - (ttl * 2 * 60)
        stale = [
            key
            for key, info in self.sessions.items()
            if info.get("last_token_at", 0) < cutoff
        ]
        for key in stale:
            self.sessions.pop(key, None)
        self._cleanup_shortlinks()
        if stale and self.api:
            self.api.log.info("Cleaned %d unused LiveKit sessions", len(stale))

    # --- identity / sessions ---

    @staticmethod
    def _identity_for(nick: str) -> str:
        identity = nick.replace("[", "").replace("]", "").replace("|", "_")
        return "".join(c for c in identity if c.isalnum() or c in "_-")

    def _existing_identity(self, nick: str, room: str, channel: str) -> str | None:
        ident = self._identity_for(nick)
        info = self.sessions.get((ident, room))
        if info and info.get("current_nick") == nick and info.get("channel") == channel:
            return ident
        return None

    def _track_session(
        self, identity: str, room: str, nick: str, channel: str
    ) -> None:
        now = int(time.time())
        self.sessions[(identity, room)] = {
            "current_nick": nick,
            "channel": channel,
            "created_at": now,
            "last_token_at": now,
        }

    def _touch_session(self, identity: str, room: str) -> None:
        info = self.sessions.get((identity, room))
        if info:
            info["last_token_at"] = int(time.time())

    def _sessions_for_nick(self, nick: str) -> list[tuple[str, str, str]]:
        out = []
        for (ident, room), info in self.sessions.items():
            if info.get("current_nick") == nick:
                out.append((ident, room, info.get("channel") or room))
        return out

    # --- webhook events ---

    async def _on_webhook_event(self, event: str, payload: dict[str, Any]) -> None:
        assert self.api is not None
        room = payload.get("room") or {}
        room_name = room.get("name") or "Unknown"
        room_sid = room.get("sid")
        participant = payload.get("participant") or {}
        pname = participant.get("name") or participant.get("identity") or "Unknown"
        identity = participant.get("identity") or pname

        if event == "room_started":
            if self.lk:
                self.lk.note_room_started(room_name, room)
                if not self.lk.should_announce_lifecycle(
                    "room_started", room_name, str(room_sid or "")
                ):
                    return
            await self._announce("info", f"🏠 LiveKit room started: \x02{room_name}\x02")
        elif event == "room_finished":
            if self.lk:
                self.lk.note_room_finished(room_name)
                if not self.lk.should_announce_lifecycle(
                    "room_finished", room_name, str(room_sid or "")
                ):
                    return
            dur = format_duration(room)
            await self._announce(
                "info", f"🏠 LiveKit room ended: \x02{room_name}\x02{dur}"
            )
        elif event == "participant_joined":
            if self.lk:
                self.lk.note_participant(room_name, identity, pname, joined=True)
            if self._room_allowed(room_name):
                await self._announce_room(
                    room_name, f"🎥 \x02{pname}\x02 joined the video chat"
                )
            else:
                await self._announce(
                    "debug", f"👋 \x02{pname}\x02 joined room \x02{room_name}\x02"
                )
        elif event == "participant_left":
            if self.lk:
                self.lk.note_participant(room_name, identity, pname, joined=False)
            if self._room_allowed(room_name):
                await self._announce_room(
                    room_name, f"📺 \x02{pname}\x02 left the video chat"
                )
            else:
                await self._announce(
                    "debug", f"👋 \x02{pname}\x02 left room \x02{room_name}\x02"
                )
        elif event == "track_published":
            track = payload.get("track") or {}
            t = (track.get("type") or "").lower()
            if t in ("video", "audio"):
                emoji = "📹" if t == "video" else "🎤"
                await self._announce(
                    "debug",
                    f"{emoji} \x02{pname}\x02 started {t} in \x02{room_name}\x02",
                )
        elif event == "track_unpublished":
            track = payload.get("track") or {}
            t = (track.get("type") or "").lower()
            if t in ("video", "audio"):
                emoji = "📹" if t == "video" else "🎤"
                await self._announce(
                    "debug",
                    f"{emoji} \x02{pname}\x02 stopped {t} in \x02{room_name}\x02",
                )
        elif event == "recording_started":
            await self._announce(
                "info", f"🎬 Recording started for room \x02{room_name}\x02"
            )
        elif event == "recording_finished":
            egress = payload.get("egress_info") or {}
            path = (egress.get("file") or {}).get("filename") or "Unknown"
            await self._announce(
                "info",
                f"🎬 Recording finished for room \x02{room_name}\x02 | File: {path}",
            )
        else:
            self.api.log.debug("Unhandled LiveKit webhook event: %s", event)

    # --- IRC presence hooks ---

    @on("user_nick")
    async def on_nick(self, old: str | None = None, new: str | None = None, **_) -> None:
        if not old or not new or not self.lk:
            return
        for (ident, room), info in list(self.sessions.items()):
            if info.get("current_nick") != old:
                continue
            info["current_nick"] = new
            meta = (
                f"irc_nick:{new};channel:{info.get('channel')};"
                f"session_start:{info.get('created_at', int(time.time()))}"
            )
            try:
                await self.lk.update_participant_metadata(room, ident, meta)
            except Exception:
                if self.api:
                    self.api.log.debug(
                        "metadata update failed for %s", ident, exc_info=True
                    )

    @on("channel_mode")
    async def on_channel_mode(
        self,
        channel: str | None = None,
        changes: list[Any] | None = None,
        **_,
    ) -> None:
        if not channel or not changes or not self.lk:
            return
        for change in changes:
            if getattr(change, "category", None) != "prefix":
                continue
            if getattr(change, "mode", None) != "o":
                continue
            nick = getattr(change, "param", None)
            if not nick:
                continue
            is_admin = bool(getattr(change, "add", False))
            for ident, room, sess_chan in self._sessions_for_nick(nick):
                if not self.api or not self.api.nicks_equal(sess_chan, channel):
                    continue
                try:
                    await self.lk.update_participant_permissions(
                        room, ident, is_admin=is_admin
                    )
                except Exception:
                    if self.api:
                        self.api.log.debug(
                            "permission update failed for %s", nick, exc_info=True
                        )

    async def _user_left(self, nick: str, channel: str, reason: str) -> None:
        assert self.api is not None
        if not self.lk:
            return
        for ident, room, sess_chan in self._sessions_for_nick(nick):
            if not self.api.nicks_equal(sess_chan, channel):
                continue
            try:
                await self.lk.remove_participant(room, ident)
            except Exception:
                self.api.log.debug(
                    "remove_participant failed for %s", nick, exc_info=True
                )
            self.sessions.pop((ident, room), None)
            await self.api.privmsg(
                channel, f"🚪 {nick} has {reason} the chat and video room."
            )

    @on("user_part")
    async def on_part(
        self, nick: str | None = None, channel: str | None = None, **_
    ) -> None:
        if nick and channel:
            await self._user_left(nick, channel, "left")

    @on("user_kick")
    async def on_kick(
        self, nick: str | None = None, channel: str | None = None, **_
    ) -> None:
        if nick and channel:
            await self._user_left(nick, channel, "been kicked from")

    @on("user_quit")
    async def on_quit(
        self, nick: str | None = None, channels: list[str] | None = None, **_
    ) -> None:
        if not nick:
            return
        for ch in channels or []:
            await self._user_left(nick, ch, "quit")

    # --- commands ---

    @on("privmsg")
    async def on_privmsg(
        self,
        nick: str | None = None,
        user: str | None = None,
        host: str | None = None,
        target: str | None = None,
        text: str | None = None,
        account: str | None = None,
        **_,
    ) -> None:
        assert self.api is not None
        text = text or ""
        if not nick or not text.startswith(self._cmd_prefix):
            return
        body = text[len(self._cmd_prefix) :].strip()
        parts = body.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        is_chan = bool(target and target[:1] in ("#", "&"))
        reply_to = target if is_chan else nick

        admin_payload = {
            "nick": nick,
            "user": user,
            "host": host,
            "account": account,
        }

        if cmd == "join":
            await self._cmd_join(nick, target, args, is_chan)
        elif cmd == "rooms":
            await self._cmd_rooms(reply_to)
        elif cmd == "createroom":
            if not (
                self.api.is_admin(admin_payload)
                or (is_chan and target and self.api.is_channel_op(target, nick))
            ):
                return
            await self._cmd_createroom(reply_to, args)
        elif cmd == "deleteroom":
            if not (
                self.api.is_admin(admin_payload)
                or (is_chan and target and self.api.is_channel_op(target, nick))
            ):
                return
            await self._cmd_deleteroom(reply_to, args)

    async def _cmd_join(
        self,
        nick: str,
        target: str | None,
        args: list[str],
        is_chan: bool,
    ) -> None:
        assert self.api is not None
        if not self.lk:
            await self.api.privmsg(nick, "❌ LiveKit is not configured.")
            return

        if is_chan:
            room_name = target or ""
            if not self._room_allowed(room_name):
                await self.api.privmsg(
                    target or nick, "❌ This channel is not configured for LiveKit rooms."
                )
                return
        else:
            if not args:
                await self.api.privmsg(
                    nick,
                    f"❌ Usage: {self._cmd_prefix}join <room_name>",
                )
                return
            room_name = args[0]
            if not self._room_allowed(room_name):
                avail = ", ".join(ch for ch, _ in self._channels) or "(none)"
                await self.api.privmsg(
                    nick, f"❌ Room '{room_name}' is not available. Available: {avail}"
                )
                return

        channel = room_name
        identity = self._existing_identity(nick, room_name, channel)
        if identity:
            self._touch_session(identity, room_name)
        else:
            identity = self._identity_for(nick)

        is_op = self.api.is_channel_op(channel, nick)
        token_kwargs: dict[str, Any] = {
            "can_publish": True,
            "can_subscribe": True,
            "can_publish_data": True,
        }
        if is_op:
            token_kwargs.update({"recorder": True, "agent": True})

        try:
            try:
                await self.lk.create_room(room_name)
            except Exception as exc:
                # already exists is fine
                self.api.log.debug("create_room on join: %s", exc)
            token = self.lk.generate_access_token(
                room_name, identity, **token_kwargs
            )
        except Exception as exc:
            self.api.log.exception("join token failed")
            await self.api.privmsg(nick, f"❌ Failed to generate join URL: {exc}")
            return

        if (identity, room_name) not in self.sessions:
            self._track_session(identity, room_name, nick, channel)
            meta = (
                f"irc_nick:{nick};channel:{channel};"
                f"session_start:{self.sessions[(identity, room_name)]['created_at']}"
            )
            try:
                await self.lk.update_participant_metadata(room_name, identity, meta)
            except Exception:
                pass
        else:
            self._touch_session(identity, room_name)

        url = self._build_join_url(token)
        url = await self._shorten_join_url(url)
        await self.api.privmsg(nick, f"🎥 LiveKit Room: {room_name}")
        await self.api.privmsg(nick, url)

    async def _cmd_rooms(self, reply_to: str) -> None:
        assert self.api is not None
        if not self.lk:
            await self.api.privmsg(reply_to, "❌ LiveKit is not configured.")
            return
        rooms = self.lk.get_active_rooms()
        if not rooms:
            await self.api.privmsg(reply_to, "📺 No active LiveKit rooms found.")
            return
        for room in rooms:
            n = room.get("num_participants") or len(room.get("participants") or [])
            await self.api.privmsg(
                reply_to,
                f"🏠 \x02{room['name']}\x02 | 👥 {n} participants | "
                f"🕐 Created: {room.get('creation_time', 'Unknown')}",
            )
            for p in room.get("participants") or []:
                emoji = "🟢" if str(p.get("state", "")).lower() == "active" else "⚪"
                await self.api.privmsg(reply_to, f"  {emoji} {p.get('name') or p.get('identity')}")

    async def _cmd_createroom(self, reply_to: str, args: list[str]) -> None:
        assert self.api is not None
        if not self.lk:
            await self.api.privmsg(reply_to, "❌ LiveKit is not configured.")
            return
        if not args:
            await self.api.privmsg(
                reply_to,
                f"❌ Usage: {self._cmd_prefix}createroom <room_name> [max_participants]",
            )
            return
        room_name = args[0]
        if len(room_name) > 50:
            await self.api.privmsg(reply_to, "❌ Room name cannot exceed 50 characters")
            return
        kwargs: dict[str, Any] = {}
        if len(args) > 1:
            try:
                max_p = int(args[1])
            except ValueError:
                await self.api.privmsg(reply_to, "❌ Max participants must be a number")
                return
            if max_p < 1 or max_p > 100:
                await self.api.privmsg(
                    reply_to, "❌ Max participants must be between 1 and 100"
                )
                return
            kwargs["max_participants"] = max_p
        try:
            await self.lk.create_room(room_name, **kwargs)
            extra = (
                f" | Max participants: {kwargs['max_participants']}" if kwargs else ""
            )
            await self.api.privmsg(
                reply_to, f"✅ Room \x02{room_name}\x02 created{extra}"
            )
        except Exception as exc:
            if "already exists" in str(exc).lower():
                await self.api.privmsg(
                    reply_to, f"❌ Room \x02{room_name}\x02 already exists"
                )
            else:
                self.api.log.exception("createroom failed")
                await self.api.privmsg(reply_to, f"❌ Failed to create room: {exc}")

    async def _cmd_deleteroom(self, reply_to: str, args: list[str]) -> None:
        assert self.api is not None
        if not self.lk:
            await self.api.privmsg(reply_to, "❌ LiveKit is not configured.")
            return
        if not args:
            await self.api.privmsg(
                reply_to, f"❌ Usage: {self._cmd_prefix}deleteroom <room_name>"
            )
            return
        room_name = args[0]
        rooms = self.lk.get_active_rooms()
        match = next(
            (r for r in rooms if r["name"].lower() == room_name.lower()), None
        )
        if match:
            room_name = match["name"]
            n = match.get("num_participants") or len(match.get("participants") or [])
            if n:
                await self.api.privmsg(
                    reply_to,
                    f"⚠️ Warning: Room \x02{room_name}\x02 has {n} active participants",
                )
        try:
            await self.lk.delete_room(room_name)
            await self.api.privmsg(reply_to, f"✅ Room \x02{room_name}\x02 deleted")
        except Exception as exc:
            self.api.log.exception("deleteroom failed")
            await self.api.privmsg(reply_to, f"❌ Failed to delete room: {exc}")
