"""High-level IRC client: registration, state, events, WHO/modes."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pybot.irc.caps import CapNegotiator
from pybot.irc.connection import Connection
from pybot.irc.flood import TokenBucket
from pybot.irc.isupport import ISupport
from pybot.irc.modes import parse_mode_string
from pybot.irc.protocol import Message, format_line, parse_message
from pybot.irc.registration import send_registration
from pybot.irc.sasl import SaslAuth
from pybot.irc.state import StateJournal, normalize_account
from pybot.irc.who import WHOX_FLAGS, WhoManager
from pybot.logging_setup import RawLogger

log = logging.getLogger("pybot.irc.client")

# Pre-registration nick failures we recover from by trying another nick
_NICK_FAIL_NUMERICS = frozenset({"432", "433", "437"})
_MAX_NICK_ATTEMPTS = 30


def fit_nick(nick: str, nicklen: int) -> str:
    """Truncate nick to ISUPPORT NICKLEN."""
    nicklen = max(1, nicklen)
    return (nick or "bot")[:nicklen]


def dashed_nick(primary: str, dashes: int, *, nicklen: int) -> str:
    """Build ``primary`` + N dashes, truncated to fit nicklen."""
    dashes = max(1, dashes)
    suffix = "-" * dashes
    keep = max(1, nicklen - len(suffix))
    return fit_nick(primary[:keep] + suffix, nicklen)


class IRCClient:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        timers: Any = None,
        raw: RawLogger | None = None,
    ) -> None:
        self.config = config
        flood_cfg = config.get("flood") or {}
        self.flood = TokenBucket(
            burst=float(flood_cfg.get("burst", 5)),
            rate=float(flood_cfg.get("rate", 1.0)),
        )
        self.raw = raw or RawLogger()
        self.conn = Connection(
            config["host"],
            int(config["port"]),
            tls=bool(config.get("tls")),
            tls_verify=bool(config.get("tls_verify", True)),
            bindhost=config.get("bindhost") or None,
            flood=self.flood,
            raw=self.raw,
        )
        self.isupport = ISupport()
        self.state = StateJournal(self.isupport)
        self.caps = CapNegotiator(self)
        self.who = WhoManager(self)
        self.timers = timers
        self._emit = emit
        self._sasl: SaslAuth | None = None
        self.registered = False
        self.desired_nick = config.get("nick", "pybot")
        self.altnick = (config.get("altnick") or "").strip() or None
        self.nick = self.desired_nick
        self._alt_tried = False
        self._dash_count = 0
        self._nick_attempts = 0
        self._ison_handle = None
        self._reclaim_target: str | None = None
        self._channels_to_join: list[tuple[str, str | None]] = self._parse_channel_list(
            config.get("channels") or []
        )
        self._welcome_event = None

    @staticmethod
    def _parse_channel_list(
        channels_cfg: list[Any] | None,
    ) -> list[tuple[str, str | None]]:
        """Normalize config channels to (name, key|None)."""
        out: list[tuple[str, str | None]] = []
        for entry in channels_cfg or []:
            if isinstance(entry, str):
                name = entry.strip()
                if name:
                    out.append((name, None))
            elif isinstance(entry, tuple) and len(entry) == 2:
                name, key = entry
                name = str(name).strip()
                if not name:
                    continue
                if key is not None:
                    key = str(key) or None
                out.append((name, key))
            elif isinstance(entry, dict):
                name = (entry.get("name") or entry.get("channel") or "").strip()
                if not name:
                    continue
                key = entry.get("key") or None
                if key is not None:
                    key = str(key) or None
                out.append((name, key))
        return out

    def set_channels_to_join(self, channels_cfg: list[Any] | None) -> None:
        self._channels_to_join = self._parse_channel_list(channels_cfg)

    @property
    def sasl_enabled(self) -> bool:
        sasl = self.config.get("sasl") or {}
        return bool(sasl.get("enabled"))

    def on_primary_nick(self) -> bool:
        return self.isupport.equal(self.nick, self.desired_nick)

    def on_alt_nick(self) -> bool:
        return bool(self.altnick) and self.isupport.equal(self.nick, self.altnick)

    async def emit(self, event: str, **payload: Any) -> None:
        if self._emit:
            await self._emit(event, payload)

    async def connect(self) -> None:
        self.registered = False
        self._alt_tried = False
        self._dash_count = 0
        self._nick_attempts = 0
        self._reclaim_target = None
        self.stop_ison_poll()
        self.desired_nick = self.config.get("nick", "pybot")
        self.altnick = (self.config.get("altnick") or "").strip() or None
        if self.altnick and self.isupport.equal(self.altnick, self.desired_nick):
            self.altnick = None
        self.nick = self.desired_nick
        self.conn.set_handlers(self._on_line, self._on_disconnect)
        await self.conn.connect()
        await self.caps.start()
        await send_registration(
            self,
            nick=self.nick,
            username=self.config.get("username", "pybot"),
            realname=self.config.get("realname", "pybot"),
            password=self.config.get("password"),
        )

    async def disconnect(self, message: str = "Quit") -> None:
        self.stop_ison_poll()
        try:
            if self.conn.connected:
                await self.send("QUIT", message)
        except Exception:
            pass
        await self.who.stop()
        await self.conn.close()

    async def _on_disconnect(self, err: Exception | None) -> None:
        self.registered = False
        self.stop_ison_poll()
        await self.emit("disconnect", error=str(err) if err else None)

    async def send(self, command: str, *params: str) -> None:
        line = format_line(command, *params)
        await self.conn.send_raw(line)

    async def privmsg(self, target: str, text: str) -> None:
        await self.send("PRIVMSG", target, text)

    async def notice(self, target: str, text: str) -> None:
        await self.send("NOTICE", target, text)

    async def join(self, channel: str, key: str | None = None) -> None:
        if key:
            await self.send("JOIN", channel, key)
        else:
            await self.send("JOIN", channel)

    async def part(self, channel: str, message: str | None = None) -> None:
        if message:
            await self.send("PART", channel, message)
        else:
            await self.send("PART", channel)

    async def sync_channels(
        self, channels_cfg: list[Any] | None = None
    ) -> None:
        """Join/part so joined channels match the configured list (hot rehash)."""
        if not self.registered or not self.conn.connected:
            return
        desired = (
            self._parse_channel_list(channels_cfg)
            if channels_cfg is not None
            else list(self._channels_to_join)
        )
        self._channels_to_join = desired
        desired_map = {
            self.isupport.casefold(name): (name, key) for name, key in desired
        }
        current = {
            self.isupport.casefold(ch.name): ch.name
            for ch in self.state.channels.values()
        }
        for key, (name, ch_key) in desired_map.items():
            if key not in current:
                log.info("Config sync: JOIN %s", name)
                await self.join(name, ch_key)
        for key, name in current.items():
            if key not in desired_map:
                log.info("Config sync: PART %s", name)
                await self.part(name, "config reload")

    async def mode(self, target: str, *args: str) -> None:
        await self.send("MODE", target, *args)

    async def begin_sasl(self) -> None:
        """Start SASL without blocking the read loop; CAP END follows on completion."""
        sasl_cfg = self.config.get("sasl") or {}
        self._sasl = SaslAuth(
            self,
            username=sasl_cfg.get("username") or self.config.get("nick", "pybot"),
            password=sasl_cfg.get("password") or "",
            mechanism=sasl_cfg.get("mechanism") or "auto",
        )
        await self._sasl.begin()

    def configure_flood(self, burst: float | None = None, rate: float | None = None) -> None:
        self.flood.configure(burst=burst, rate=rate)

    async def _on_line(self, line: str) -> None:
        msg = parse_message(line)
        await self._dispatch(msg)

    async def _dispatch(self, msg: Message) -> None:
        if await self.caps.handle(msg):
            return

        if self._sasl and not self._sasl.done:
            if await self._sasl.handle(msg):
                return

        cmd = msg.command

        if cmd == "PING":
            await self.send("PONG", msg.trailing or (msg.params[0] if msg.params else ""))
            return

        if cmd == "001":
            self.registered = True
            if msg.params:
                self.nick = msg.params[0]
                self.state.set_nick(self.nick)
            log.info("Registered as %s", self.nick)
            log.debug(
                "State: self nick=%s caps=%s",
                self.nick,
                sorted(self.caps.acked),
            )
            await self.emit("registered", nick=self.nick)
            for ch, key in self._channels_to_join:
                await self.join(ch, key)
            # Start WHO poll only when account identity cannot be tracked via caps
            if self.who.needs_account_poll():
                interval = float(self.config.get("who_poll_seconds") or 60)
                self.who.start_poll(
                    interval, lambda: [c.name for c in self.state.channels.values()]
                )
            self._maybe_start_ison_poll()
            return

        if cmd in _NICK_FAIL_NUMERICS:
            await self._handle_nick_unavailable(msg)
            return

        if cmd == "303":
            await self._handle_ison(msg)
            return

        if cmd == "005":
            # params: nick token token ... :are supported...
            tokens = msg.params[1:]
            if tokens and tokens[-1].lower().startswith("are supported"):
                tokens = tokens[:-1]
            self.isupport.parse_tokens(tokens)
            log.debug(
                "ISUPPORT: casemap=%s nicklen=%s whox=%s prefix=%s%s chanmodes=%s",
                self.isupport.casemapping,
                self.isupport.nicklen,
                self.isupport.whox,
                self.isupport.prefix_modes,
                self.isupport.prefix_symbols,
                ",".join(self.isupport.chanmodes),
            )
            await self.emit("isupport", isupport=self.isupport)
            return

        if cmd == "PRIVMSG":
            await self._handle_privmsg(msg, notice=False)
            return
        if cmd == "NOTICE":
            await self._handle_privmsg(msg, notice=True)
            return

        if cmd == "JOIN":
            await self._handle_join(msg)
            return
        if cmd == "INVITE":
            await self._handle_invite(msg)
            return
        if cmd == "PART":
            await self._handle_part(msg)
            return
        if cmd == "QUIT":
            await self._handle_quit(msg)
            return
        if cmd == "NICK":
            await self._handle_nick(msg)
            return
        if cmd == "KICK":
            await self._handle_kick(msg)
            return
        if cmd == "MODE":
            await self._handle_mode(msg)
            return
        if cmd == "ACCOUNT":
            await self._handle_account(msg)
            return
        if cmd == "AWAY":
            await self._handle_away(msg)
            return
        if cmd == "TOPIC":
            if len(msg.params) >= 1:
                ch = self.state.ensure_channel(msg.params[0])
                ch.topic = msg.trailing
            return

        # NAMES
        if cmd == "353":
            await self._handle_names(msg)
            return
        if cmd == "366":
            # end of names — run WHO
            channel = msg.params[1] if len(msg.params) > 1 else ""
            ch = self.state.get_channel(channel) if channel else None
            members = list(ch.members.values()) if ch else []
            log.debug(
                "State NAMES end %s: %d member(s): %s",
                channel,
                len(members),
                ", ".join(
                    f"{m.prefix_symbols(self.isupport)}{m.nick}" for m in members
                )
                or "(none)",
            )
            await self.emit("names_end", channel=channel)
            if channel:
                await self.who.query(channel)
            return

        # WHO
        if cmd == "352":
            await self._handle_who_352(msg)
            return
        if cmd == "354":
            await self._handle_who_354(msg)
            return
        if cmd == "315":
            target = msg.params[1] if len(msg.params) > 1 else None
            self.who.on_end(target)
            self._debug_dump_state(target)
            await self.emit("who_end", target=target)
            return

        # Generic numeric
        if cmd.isdigit():
            await self.emit(
                "numeric",
                code=int(cmd),
                params=msg.params,
                tags=msg.tags,
                prefix=msg.prefix.raw if msg.prefix else None,
            )
            return

        await self.emit(
            "raw_message",
            command=cmd,
            params=msg.params,
            tags=msg.tags,
            nick=msg.source_nick,
        )

    async def _handle_invite(self, msg: Message) -> None:
        """Auto-join configured channels when invited."""
        if not self.registered or not self.conn.connected:
            return
        if len(msg.params) < 2:
            return
        invited_nick = msg.params[0]
        channel = msg.params[1]
        if not self.isupport.equal(invited_nick, self.nick):
            return

        allowed = {
            self.isupport.casefold(name)
            for name, _key in self._channels_to_join
        }
        if self.isupport.casefold(channel) not in allowed:
            log.debug("Ignoring INVITE to unconfigured channel %s", channel)
            return

        existing = {self.isupport.casefold(ch.name) for ch in self.state.channels.values()}
        if self.isupport.casefold(channel) in existing:
            return

        log.info("Auto-join invited channel %s", channel)
        await self.join(channel)

    async def _handle_nick_unavailable(self, msg: Message) -> None:
        """433/432/437 — try altnick, then nick-, nick--, …"""
        failed = msg.params[1] if len(msg.params) > 1 else self.nick
        log.warning(
            "Nick unavailable (%s): %s — %s",
            msg.command,
            failed,
            msg.trailing,
        )
        await self.emit(
            "nick_unavailable",
            code=int(msg.command),
            nick=failed,
            reason=msg.trailing,
            registered=self.registered,
        )
        if self.registered:
            # Reclaim attempt lost the race; keep polling
            if self._reclaim_target and self.isupport.equal(
                failed, self._reclaim_target
            ):
                self._reclaim_target = None
            return

        if self._nick_attempts >= _MAX_NICK_ATTEMPTS:
            log.error("Gave up after %s nick attempts", _MAX_NICK_ATTEMPTS)
            await self.disconnect("nick unavailable")
            return

        next_nick = self._next_registration_nick(failed)
        if not next_nick:
            log.error("No further nick fallbacks available")
            await self.disconnect("nick unavailable")
            return
        self._nick_attempts += 1
        self.nick = next_nick
        log.info("Retrying nick as %s", next_nick)
        await self.send("NICK", next_nick)

    def _next_registration_nick(self, failed: str) -> str | None:
        nicklen = self.isupport.nicklen
        primary = self.desired_nick
        # After primary fails → configured altnick once
        if (
            self.altnick
            and not self._alt_tried
            and not self.isupport.equal(failed, self.altnick)
        ):
            self._alt_tried = True
            return fit_nick(self.altnick, nicklen)
        # Then primary + "-", primary + "--", …
        self._dash_count += 1
        candidate = dashed_nick(primary, self._dash_count, nicklen=nicklen)
        if self.isupport.equal(candidate, self.nick):
            self._dash_count += 1
            candidate = dashed_nick(primary, self._dash_count, nicklen=nicklen)
        return candidate

    def _maybe_start_ison_poll(self) -> None:
        if self.on_primary_nick():
            self.stop_ison_poll()
            return
        if self._ison_handle is not None:
            return
        if self.timers is None:
            return
        interval = float(self.config.get("ison_poll_seconds") or 30)
        if interval <= 0:
            return

        async def _tick() -> None:
            await self._ison_poll()

        self._ison_handle = self.timers.every(
            interval,
            _tick,
            name="ison_nick_recover",
            owner="core:ison",
            immediate=True,
        )
        log.info(
            "ISON nick recovery started (every %ss); want %s%s",
            interval,
            self.desired_nick,
            f" / {self.altnick}" if self.altnick and not self.on_alt_nick() else "",
        )

    def stop_ison_poll(self) -> None:
        if self._ison_handle is not None and self.timers:
            self.timers.cancel(self._ison_handle)
            self._ison_handle = None

    async def _ison_poll(self) -> None:
        if not self.registered or not self.conn.connected:
            return
        if self.on_primary_nick():
            self.stop_ison_poll()
            return
        if self._reclaim_target:
            return  # wait for prior NICK attempt to resolve
        targets = [self.desired_nick]
        if self.altnick and not self.on_alt_nick():
            targets.append(self.altnick)
        await self.send("ISON", *targets)

    async def _handle_ison(self, msg: Message) -> None:
        # 303 me :nick1 nick2   (online nicks from the query; empty = none online)
        online = {
            self.isupport.casefold(n)
            for n in (msg.trailing or "").split()
            if n
        }
        if self.on_primary_nick():
            self.stop_ison_poll()
            return

        # Prefer primary, then altnick (if not already on it)
        candidates: list[str] = [self.desired_nick]
        if self.altnick and not self.on_alt_nick():
            candidates.append(self.altnick)

        log.debug(
            "ISON reply online=%s (watching primary=%s alt=%s current=%s)",
            sorted(online) or "[]",
            self.desired_nick,
            self.altnick,
            self.nick,
        )
        for candidate in candidates:
            if self.isupport.casefold(candidate) in online:
                continue
            if self.isupport.equal(candidate, self.nick):
                continue
            log.info("ISON: %s is free — reclaiming", candidate)
            self._reclaim_target = candidate
            await self.send("NICK", candidate)
            return

    async def _handle_privmsg(self, msg: Message, *, notice: bool) -> None:
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        nick = msg.source_nick
        if nick and "account" in msg.tags:
            # empty / missing tag value ⇒ no account
            self.state.set_account(nick, msg.tags.get("account"))
        event = "notice" if notice else "privmsg"
        await self.emit(
            event,
            nick=nick,
            user=msg.prefix.user if msg.prefix else None,
            host=msg.prefix.host if msg.prefix else None,
            target=target,
            text=text,
            tags=msg.tags,
            account=msg.tags.get("account"),
        )

    async def _handle_join(self, msg: Message) -> None:
        nick = msg.source_nick
        if not nick:
            return
        channel = msg.params[0] if msg.params else msg.trailing
        account = None
        account_set = False
        realname = None
        # extended-join: JOIN #chan account :realname
        if self.caps.has("extended-join") and len(msg.params) >= 2:
            account = normalize_account(msg.params[1])
            account_set = True
            if len(msg.params) >= 3:
                realname = msg.params[2]
        if "account" in msg.tags:
            account = normalize_account(msg.tags.get("account"))
            account_set = True
        user = self.state.ensure_user(nick)
        if msg.prefix:
            user.user = msg.prefix.user
            user.host = msg.prefix.host
        if account_set:
            user.account = account
        if realname is not None:
            user.realname = realname
        self.state.add_member(channel, nick)
        log.debug("State JOIN %s → %s | %s", nick, channel, user.debug_str())
        await self.emit(
            "user_join",
            nick=nick,
            channel=channel,
            account=user.account,
            realname=user.realname,
        )
        # If we joined, WHO will run on 366

    async def _handle_part(self, msg: Message) -> None:
        nick = msg.source_nick
        if not nick or not msg.params:
            return
        channel = msg.params[0]
        reason = msg.params[1] if len(msg.params) > 1 else ""
        self.state.remove_member(channel, nick)
        log.debug("State PART %s ← %s (%s)", nick, channel, reason)
        await self.emit("user_part", nick=nick, channel=channel, reason=reason)

    async def _handle_quit(self, msg: Message) -> None:
        nick = msg.source_nick
        if not nick:
            return
        reason = msg.trailing
        channels = list(self.state.get_user(nick).channels) if self.state.get_user(nick) else []
        self.state.remove_user_everywhere(nick)
        log.debug("State QUIT %s from %s (%s)", nick, channels, reason)
        await self.emit("user_quit", nick=nick, reason=reason, channels=channels)

    async def _handle_nick(self, msg: Message) -> None:
        old = msg.source_nick
        new = msg.params[0] if msg.params else msg.trailing
        if not old or not new:
            return
        self.state.rename_user(old, new)
        log.debug("State NICK %s → %s", old, new)
        if old and self.isupport.equal(old, self.nick):
            self.nick = new
            self.state.set_nick(new)
            if self._reclaim_target and self.isupport.equal(new, self._reclaim_target):
                self._reclaim_target = None
            if self.on_primary_nick():
                log.info("Reclaimed primary nick %s", self.nick)
                self.stop_ison_poll()
            else:
                self._maybe_start_ison_poll()
        await self.emit("user_nick", old=old, new=new)

    async def _handle_kick(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return
        channel, nick = msg.params[0], msg.params[1]
        reason = msg.params[2] if len(msg.params) > 2 else ""
        self.state.remove_member(channel, nick)
        log.debug(
            "State KICK %s ← %s by %s (%s)",
            nick,
            channel,
            msg.source_nick,
            reason,
        )
        await self.emit(
            "user_kick",
            channel=channel,
            nick=nick,
            kicker=msg.source_nick,
            reason=reason,
        )

    async def _handle_mode(self, msg: Message) -> None:
        if not msg.params:
            return
        target = msg.params[0]
        if not msg.params[1:]:
            return
        mode_string = msg.params[1]
        params = msg.params[2:]
        changes = parse_mode_string(self.isupport, mode_string, params)

        if target[:1] in self.isupport.chantypes:
            ch = self.state.ensure_channel(target)
            for change in changes:
                if change.category == "prefix" and change.param:
                    member = self.state.add_member(target, change.param)
                    if change.add:
                        member.prefixes.add(change.mode)
                    else:
                        member.prefixes.discard(change.mode)
                    log.debug(
                        "State MODE %s %s%c %s → prefixes=%s",
                        target,
                        "+" if change.add else "-",
                        change.mode,
                        change.param,
                        "".join(sorted(member.prefixes)),
                    )
                else:
                    if change.add:
                        ch.modes[change.mode] = change.param
                    else:
                        ch.modes.pop(change.mode, None)
                    log.debug(
                        "State MODE %s %s%c %s → chanmodes=%s",
                        target,
                        "+" if change.add else "-",
                        change.mode,
                        change.param or "",
                        ch.modes,
                    )
            await self.emit(
                "channel_mode",
                channel=target,
                nick=msg.source_nick,
                changes=changes,
            )
        else:
            log.debug("State umode %s %s %s", target, mode_string, params)
            await self.emit(
                "user_mode",
                target=target,
                nick=msg.source_nick,
                changes=changes,
            )

    async def _handle_account(self, msg: Message) -> None:
        nick = msg.source_nick
        if not nick:
            return
        account = normalize_account(msg.params[0] if msg.params else None)
        self.state.set_account(nick, account)
        log.debug("State ACCOUNT %s → %s", nick, account or "-")
        await self.emit("user_account", nick=nick, account=account)

    async def _handle_away(self, msg: Message) -> None:
        nick = msg.source_nick
        if not nick:
            return
        user = self.state.ensure_user(nick)
        if msg.params:
            user.away = True
            user.away_message = msg.trailing
            log.debug("State AWAY %s: %s", nick, user.away_message)
        else:
            user.away = False
            user.away_message = None
            log.debug("State AWAY %s: back", nick)

    async def _handle_names(self, msg: Message) -> None:
        # 353 nick = #chan :@nick +nick2
        if len(msg.params) < 4:
            # some send 353 nick type #chan :names
            if len(msg.params) < 3:
                return
            channel = msg.params[1]
            names = msg.params[2]
        else:
            channel = msg.params[2]
            names = msg.params[3]
        added = []
        for item in names.split():
            prefixes: set[str] = set()
            while item and item[0] in self.isupport.prefix_symbols:
                mode = self.isupport.mode_for_symbol(item[0])
                if mode:
                    prefixes.add(mode)
                item = item[1:]
            if item:
                self.state.add_member(channel, item, prefixes)
                added.append(f"{''.join(sorted(prefixes))}:{item}" if prefixes else item)
        log.debug("State NAMES %s += %s", channel, ", ".join(added) or "(empty)")

    async def _handle_who_352(self, msg: Message) -> None:
        # 352 me channel user host server nick flags :hop realname
        if len(msg.params) < 8:
            return
        channel = msg.params[1]
        user, host, nick = msg.params[2], msg.params[3], msg.params[5]
        flags = msg.params[6]
        realname = msg.trailing
        if realname and realname[0].isdigit() and " " in realname:
            realname = realname.split(" ", 1)[1]
        away = flags.startswith("G")
        oper = "*" in flags or "o" in flags or "O" in flags
        u = self.state.update_who(
            nick,
            user=user,
            host=host,
            realname=realname,
            channel=channel if channel != "*" else None,
            away=away,
            oper=oper,
        )
        log.debug("State WHO %s", u.debug_str())

    async def _handle_who_354(self, msg: Message) -> None:
        # 354 me <fields matching WHOX_FLAGS order tcuhnaro>
        # t c u h n a r o → type channel user host nick account realname oper
        params = msg.params[1:]  # skip our nick
        fields = list(WHOX_FLAGS)
        if len(params) < len(fields):
            # pad
            params = params + [""] * (len(fields) - len(params))
        data = dict(zip(fields, params))
        nick = data.get("n") or ""
        if not nick:
            return
        # WHOX a=0 or a=* → not logged in (stored as None)
        account = data.get("a")
        channel = data.get("c") or None
        if channel == "*":
            channel = None
        oper_value = data.get("o")
        oper = False
        if oper_value is not None:
            text = str(oper_value).strip()
            oper = text not in ("", "0", "false", "False", "n", "N", "no", "No")
        u = self.state.update_who(
            nick,
            user=data.get("u"),
            host=data.get("h"),
            account=account,
            realname=data.get("r"),
            channel=channel,
            oper=oper,
        )
        log.debug("State WHOX %s", u.debug_str())

    def _debug_dump_state(self, target: str | None = None) -> None:
        """Dump journalled users (and channel if target is one) at DEBUG."""
        if not log.isEnabledFor(logging.DEBUG):
            return
        if target and target[:1] in self.isupport.chantypes:
            summary = self.state.dump_channel(target)
            if summary:
                log.debug("State dump channel %s", summary)
        users = self.state.dump_users()
        log.debug("State dump users (%d):", len(users))
        for line in users:
            log.debug("  %s", line)
