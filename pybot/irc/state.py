"""User and channel journals with case-mapped keys."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pybot.irc.isupport import ISupport

# Sentinel: omit this field in update_who
_MISSING: Any = object()


def normalize_account(value: str | None) -> str | None:
    """IRC ``*`` / ``0`` mean not logged in → ``None``. Named account otherwise."""
    if value is None or value in ("*", "0", ""):
        return None
    return value


@dataclass
class User:
    nick: str
    user: str | None = None
    host: str | None = None
    # None = no account / not logged in (or never learned). Else services account name.
    account: str | None = None
    oper: bool = False
    away: bool = False
    away_message: str | None = None
    realname: str | None = None
    channels: set[str] = field(default_factory=set)

    @property
    def hostmask(self) -> str:
        u = self.user or "*"
        h = self.host or "*"
        return f"{self.nick}!{u}@{h}"

    def debug_str(self) -> str:
        """Python-repr dump of all fields (account=None when not logged in)."""
        # Sort channels for stable logs; dataclass repr already shows None correctly
        chans = sorted(self.channels)
        return (
            f"User(nick={self.nick!r}, user={self.user!r}, host={self.host!r}, "
            f"account={self.account!r}, oper={self.oper!r}, away={self.away!r}, "
            f"away_message={self.away_message!r}, realname={self.realname!r}, "
            f"channels={chans!r})"
        )


@dataclass
class ChannelMember:
    nick: str
    prefixes: set[str] = field(default_factory=set)  # mode letters e.g. {'o','v'}

    def prefix_symbols(self, isupport: ISupport) -> str:
        # highest first as in PREFIX order
        out = []
        for mode, sym in zip(isupport.prefix_modes, isupport.prefix_symbols):
            if mode in self.prefixes:
                out.append(sym)
        return "".join(out)


@dataclass
class Channel:
    name: str
    topic: str | None = None
    modes: dict[str, str | None] = field(default_factory=dict)  # letter -> param or None
    members: dict[str, ChannelMember] = field(default_factory=dict)  # casefolded nick


class StateJournal:
    def __init__(self, isupport: ISupport) -> None:
        self.isupport = isupport
        self.users: dict[str, User] = {}  # casefolded nick
        self.channels: dict[str, Channel] = {}  # casefolded channel
        self.nick: str | None = None

    def casefold(self, name: str) -> str:
        return self.isupport.casefold(name)

    def set_nick(self, nick: str) -> None:
        self.nick = nick

    def get_user(self, nick: str) -> User | None:
        return self.users.get(self.casefold(nick))

    def ensure_user(self, nick: str) -> User:
        key = self.casefold(nick)
        user = self.users.get(key)
        if user is None:
            user = User(nick=nick)
            self.users[key] = user
        else:
            user.nick = nick  # preserve latest casing
        return user

    def get_channel(self, name: str) -> Channel | None:
        return self.channels.get(self.casefold(name))

    def ensure_channel(self, name: str) -> Channel:
        key = self.casefold(name)
        ch = self.channels.get(key)
        if ch is None:
            ch = Channel(name=name)
            self.channels[key] = ch
        else:
            ch.name = name
        return ch

    def add_member(self, channel: str, nick: str, prefixes: set[str] | None = None) -> ChannelMember:
        ch = self.ensure_channel(channel)
        user = self.ensure_user(nick)
        key = self.casefold(nick)
        member = ch.members.get(key)
        if member is None:
            member = ChannelMember(nick=nick, prefixes=set(prefixes or ()))
            ch.members[key] = member
        else:
            member.nick = nick
            if prefixes is not None:
                member.prefixes = set(prefixes)
        user.channels.add(self.casefold(channel))
        return member

    def remove_member(self, channel: str, nick: str) -> None:
        ch = self.get_channel(channel)
        if not ch:
            return
        key = self.casefold(nick)
        ch.members.pop(key, None)
        user = self.get_user(nick)
        if user:
            user.channels.discard(self.casefold(channel))
            if not user.channels:
                # keep user record for account journal briefly; drop if no channels
                self.users.pop(key, None)

    def rename_user(self, old_nick: str, new_nick: str) -> User | None:
        old_key = self.casefold(old_nick)
        user = self.users.pop(old_key, None)
        if not user:
            return None
        user.nick = new_nick
        new_key = self.casefold(new_nick)
        self.users[new_key] = user
        for ch_key in list(user.channels):
            ch = self.channels.get(ch_key)
            if not ch:
                continue
            member = ch.members.pop(old_key, None)
            if member:
                member.nick = new_nick
                ch.members[new_key] = member
        return user

    def remove_user_everywhere(self, nick: str) -> None:
        key = self.casefold(nick)
        user = self.users.pop(key, None)
        if not user:
            return
        for ch_key in list(user.channels):
            ch = self.channels.get(ch_key)
            if ch:
                ch.members.pop(key, None)

    def set_account(self, nick: str, account: str | None) -> User:
        user = self.ensure_user(nick)
        user.account = normalize_account(account)
        return user

    def update_who(
        self,
        nick: str,
        *,
        user: str | None = None,
        host: str | None = None,
        account: Any = _MISSING,
        realname: str | None = None,
        channel: str | None = None,
        away: bool | None = None,
        oper: bool | None = None,
    ) -> User:
        u = self.ensure_user(nick)
        if user is not None:
            u.user = user
        if host is not None:
            u.host = host
        if account is not _MISSING:
            u.account = normalize_account(account)
        if realname is not None:
            u.realname = realname
        if away is not None:
            u.away = away
        if oper is not None:
            u.oper = bool(oper)
        if channel:
            self.add_member(channel, nick)
        return u

    def dump_users(self) -> list[str]:
        """Return one Python-repr line per known user (sorted by nick)."""
        users = sorted(self.users.values(), key=lambda u: self.casefold(u.nick))
        return [u.debug_str() for u in users]

    def dump_channel(self, name: str) -> str | None:
        ch = self.get_channel(name)
        if not ch:
            return None
        members = []
        for key, member in sorted(ch.members.items()):
            user = self.users.get(key)
            pref = member.prefix_symbols(self.isupport)
            if user:
                members.append(f"{pref}{user.debug_str()}")
            else:
                members.append(f"{pref}{member.nick!r}")
        if ch.modes:
            mode_s = "+" + "".join(sorted(ch.modes.keys()))
        else:
            mode_s = "(none)"
        return f"{ch.name!r} modes={mode_s} members={len(members)}: " + "; ".join(members)
