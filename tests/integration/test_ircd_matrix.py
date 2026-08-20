"""Parser interop across the ircd matrix (docker/ircd-matrix).

Drives the real IRCClient against several IRCd implementations
(ircd-irc2, unreal4, hybrid, ircu2, bahamut, ngircd, charybdis, inspircd)
to catch parsing/state assumptions that only ircu2 (the main integration
harness) happens to satisfy. Registration, ISUPPORT, JOIN/NAMES, WHO/WHOX
(whichever the server advertises — the two use different reply numerics
and field layouts), PRIVMSG, KICK, NICK, PART, and QUIT are asserted for
every server; MODE +o and KICK are checked best-effort since op-on-create
and mode-broadcast timing vary across implementations that old, quirky
IRCds ship.

Requires `docker compose -f docker/ircd-matrix/docker-compose.yml up -d`
(see scripts/run-ircd-matrix.sh) and `pytest --ircd-matrix`.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from pybot.core.timers import TimerEngine
from pybot.irc.client import IRCClient
from pybot.logging_setup import setup_logging
from tests.conftest import IRCD_MATRIX_SERVERS
from tests.harness.fake_client import FakeClient
from tests.harness.wait import wait_port, wait_until

pytestmark = pytest.mark.ircd_matrix

log = logging.getLogger("pybot.tests.ircd_matrix")


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


async def _make_bot(host: str, port: int, timers: TimerEngine) -> tuple[IRCClient, list]:
    events: list[tuple[str, dict]] = []

    async def emit(event: str, payload: dict) -> None:
        events.append((event, payload))

    client = IRCClient(
        {
            "host": host,
            "port": port,
            "tls": False,
            "nick": _uniq("bot"),
            "username": "pybot",
            "realname": "pybot matrix test",
            "channels": [],
            "sasl": {"enabled": False},
            "flood": {"burst": 20, "rate": 10.0},
            "who_poll_seconds": 0,
        },
        emit=emit,
        timers=timers,
    )
    await client.connect()
    # Some of these images run ident/reverse-DNS lookups on connect and only
    # give up after their own internal timeout (observed: ircd-irc2 ~70s,
    # bahamut ~38s) before completing registration.
    await wait_until(lambda: client.registered, timeout=90, desc="bot registered")
    return client, events


def _matrix_params() -> list:
    params = []
    for name in sorted(IRCD_MATRIX_SERVERS):
        marks = []
        if name == "charybdis":
            # irccom/charybdis:latest stops accept()-ing new connections
            # shortly after startup (TCP backlog fills, Recv-Q never
            # drains) — reproduced by hand outside pytest, and a container
            # restart doesn't recover it (stale pidfile refuses relaunch).
            # This is a bug in that published image, not in pybot's parser.
            marks.append(
                pytest.mark.xfail(
                    reason="irccom/charybdis:latest stops accepting connections "
                    "after startup; known-broken image, not a pybot bug",
                    strict=False,
                )
            )
        params.append(pytest.param(name, marks=marks))
    return params


@pytest.mark.asyncio
@pytest.mark.parametrize("server_name", _matrix_params())
async def test_ircd_matrix_interop(server_name: str, ircd_matrix_host: str) -> None:
    host = ircd_matrix_host
    port = IRCD_MATRIX_SERVERS[server_name]
    await wait_port(host, port, timeout=90.0)

    setup_logging({"level": "WARNING", "raw_enabled": False})
    timers = TimerEngine()
    channel = f"#{_uniq('m')[:9]}"

    bot: IRCClient | None = None
    op: FakeClient | None = None
    target: FakeClient | None = None
    victim: FakeClient | None = None
    try:
        bot, events = await _make_bot(host, port, timers)

        op = FakeClient(host=host, port=port, nick=_uniq("op"))
        target = FakeClient(host=host, port=port, nick=_uniq("tg"))
        victim = FakeClient(host=host, port=port, nick=_uniq("vc"))
        # Same ident/reverse-DNS delay as the bot (see _make_bot) applies
        # here. Connecting these sequentially rather than concurrently is
        # deliberate: unreal4 reproducibly hangs (not just slows down) when
        # 4 clients connect at once — the same class of "stops servicing
        # new connections under load" issue observed on charybdis, just
        # triggered by concurrency here instead of happening outright.
        await op.connect(timeout=90)
        await target.connect(timeout=90)
        await victim.connect(timeout=90)

        # 005 ISUPPORT must have been parsed into usable tokens.
        assert bot.isupport.casemapping, f"{server_name}: no CASEMAPPING in 005"
        assert bot.isupport.prefix_modes, f"{server_name}: no PREFIX in 005"

        # First joiner creates the channel (op on most servers).
        await op.join(channel)
        await bot.join(channel)
        await wait_until(
            lambda: bot.state.get_channel(channel) is not None,
            timeout=20,
            desc=f"{server_name}: bot channel state",
        )
        await target.join(channel)
        await victim.join(channel)
        await wait_until(
            lambda: (ch := bot.state.get_channel(channel)) is not None
            and bot.isupport.casefold(target.nick) in ch.members
            and bot.isupport.casefold(victim.nick) in ch.members,
            timeout=15,
            desc=f"{server_name}: target/victim in NAMES/JOIN journal",
        )
        ch = bot.state.get_channel(channel)
        assert ch is not None
        nicks = {m.nick.lower() for m in ch.members.values()}
        assert op.nick.lower() in nicks
        assert target.nick.lower() in nicks
        assert victim.nick.lower() in nicks
        assert bot.nick.lower() in nicks

        # WHO/WHOX: the client auto-queries on 366 (end of NAMES). Whether
        # this server advertised WHOX in 005 decides which reply numeric
        # (352 vs 354) and field layout gets parsed here.
        log.info("%s: WHOX=%s", server_name, bot.isupport.whox)

        def target_who_filled() -> bool:
            u = bot.state.get_user(target.nick)
            return u is not None and u.user is not None and u.host is not None

        await wait_until(
            target_who_filled, timeout=30, desc=f"{server_name}: WHO filled user/host"
        )
        who_user = bot.state.get_user(target.nick)
        assert who_user is not None
        # No ident server answers in this harness, so servers prefix "~"
        # per RFC 1413 convention (no-ident marker) — accept either.
        assert who_user.user in ("fake", "~fake"), who_user.user
        assert who_user.host

        # PRIVMSG parsing: nick/target/text round-trip through the event bus.
        before = len(events)
        text = "hello from matrix test"
        await op.privmsg(channel, text)
        await wait_until(
            lambda: any(
                e == "privmsg" and p.get("text") == text for e, p in events[before:]
            ),
            timeout=15,
            desc=f"{server_name}: privmsg event",
        )
        priv = next(p for e, p in events[before:] if e == "privmsg" and p.get("text") == text)
        assert priv["nick"].lower() == op.nick.lower()
        assert priv["target"].lower() == channel.lower()

        # Best-effort: MODE +o propagation. Op-on-create and mode-broadcast
        # timing vary enough across old IRCds that this is a warning, not a
        # hard failure — the parse path still gets exercised either way.
        await op.mode(channel, "+o", target.nick)

        def target_is_op() -> bool:
            c = bot.state.get_channel(channel)
            if not c:
                return False
            member = c.members.get(bot.isupport.casefold(target.nick))
            return bool(member and "o" in member.prefixes)

        try:
            await wait_until(target_is_op, timeout=10, desc="target opped")
        except TimeoutError:
            log.warning("%s: MODE +o not observed within timeout (non-fatal)", server_name)

        # Best-effort: KICK propagation and its event payload. Relies on op
        # actually holding channel ops (first-joiner-is-op is a convention,
        # not a guarantee), so this is a warning, not a hard failure. Kicks
        # `victim` specifically (not `target`) — once a user leaves the
        # only channel it shares with the bot, the bot's state prunes that
        # user's global record (real IRC visibility semantics: you stop
        # hearing about someone once you share no channel with them), which
        # would break the NICK/QUIT checks below if they ran on the same
        # nick that just got kicked.
        kick_before = len(events)
        await op.send("KICK", channel, victim.nick, "bye")

        def victim_kicked() -> bool:
            c = bot.state.get_channel(channel)
            return bool(c) and bot.isupport.casefold(victim.nick) not in c.members

        try:
            await wait_until(victim_kicked, timeout=10, desc="victim kicked")
            kick = next(
                (
                    p
                    for e, p in events[kick_before:]
                    if e == "user_kick" and p.get("nick", "").lower() == victim.nick.lower()
                ),
                None,
            )
            assert kick is not None, f"{server_name}: no user_kick event for KICK"
            assert kick["kicker"].lower() == op.nick.lower()
            assert kick["channel"].lower() == channel.lower()
        except TimeoutError:
            log.warning("%s: KICK not observed within timeout (non-fatal)", server_name)

        # NICK propagation.
        new_nick = _uniq("n")
        old_nick = target.nick
        await target.nick_change(new_nick)
        await wait_until(
            lambda: bot.state.get_user(new_nick) is not None
            and bot.state.get_user(old_nick) is None,
            timeout=15,
            desc=f"{server_name}: nick renamed in journal",
        )

        # PART / QUIT remove members from state.
        await op.part(channel, "leaving")
        await wait_until(
            lambda: (c := bot.state.get_channel(channel)) is not None
            and bot.isupport.casefold(op.nick) not in c.members,
            timeout=15,
            desc=f"{server_name}: part removed",
        )
        await target.quit("gone")
        await wait_until(
            lambda: bot.state.get_user(new_nick) is None,
            timeout=15,
            desc=f"{server_name}: quit removed user",
        )
    finally:
        timers.cancel_all()
        for c in (op, target, victim):
            if c is not None:
                try:
                    await c.quit()
                except Exception:
                    pass
        if bot is not None:
            await bot.disconnect("test done")
