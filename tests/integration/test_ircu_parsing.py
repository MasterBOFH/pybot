"""Integration tests against live ircu2 — parsing, state, modes, traffic."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from pybot.core.timers import TimerEngine
from pybot.irc.client import IRCClient
from pybot.logging_setup import setup_logging
from tests.harness.fake_client import FakeClient
from tests.harness.wait import wait_until

pytestmark = pytest.mark.integration


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


@pytest.fixture
async def bot_client(irc_server):
    host, port = irc_server
    setup_logging({"level": "WARNING", "raw_enabled": False})
    events: list[tuple[str, dict]] = []

    async def emit(event: str, payload: dict) -> None:
        events.append((event, payload))

    timers = TimerEngine()
    nick = _uniq("bot")
    client = IRCClient(
        {
            "host": host,
            "port": port,
            "tls": False,
            "nick": nick,
            "username": "pybot",
            "realname": "pybot test",
            "channels": [],
            "sasl": {"enabled": False},
            "flood": {"burst": 20, "rate": 10.0},
            "who_poll_seconds": 0,
        },
        emit=emit,
        timers=timers,
    )
    await client.connect()
    await wait_until(lambda: client.registered, timeout=20, desc="bot registered")
    client._test_events = events  # type: ignore[attr-defined]
    yield client
    timers.cancel_all()
    await client.disconnect("test done")


@pytest.fixture
async def fake_factory(irc_server):
    host, port = irc_server
    clients: list[FakeClient] = []

    async def spawn(nick_prefix: str = "f") -> FakeClient:
        c = FakeClient(host=host, port=port, nick=_uniq(nick_prefix))
        await c.connect()
        clients.append(c)
        return c

    yield spawn
    for c in clients:
        await c.quit()


@pytest.mark.asyncio
async def test_registration_and_isupport(bot_client: IRCClient) -> None:
    assert bot_client.registered
    # ircu2 should send CASEMAPPING / CHANMODES / PREFIX via 005
    assert bot_client.isupport.casemapping
    assert bot_client.isupport.prefix_modes
    assert len(bot_client.isupport.chanmodes[3]) > 0 or bot_client.isupport.has("CHANMODES")


@pytest.mark.asyncio
async def test_join_names_and_member_journal(
    bot_client: IRCClient, fake_factory
) -> None:
    channel = f"#t{_uniq('')[:8]}"
    alice = await fake_factory("a")
    bob = await fake_factory("b")

    await bot_client.join(channel)
    await wait_until(
        lambda: bot_client.state.get_channel(channel) is not None,
        timeout=30,
        desc="bot channel state",
    )

    await alice.join(channel)
    await bob.join(channel)

    await wait_until(
        lambda: bot_client.state.get_channel(channel) is not None
        and alice.nick.lower()
        in {m.nick.lower() for m in bot_client.state.get_channel(channel).members.values()},
        timeout=15,
        desc="alice in journal",
    )
    await wait_until(
        lambda: bob.nick.lower()
        in {m.nick.lower() for m in bot_client.state.get_channel(channel).members.values()},
        timeout=10,
        desc="bob in journal",
    )

    ch = bot_client.state.get_channel(channel)
    assert ch is not None
    nicks = {m.nick.lower() for m in ch.members.values()}
    assert alice.nick.lower() in nicks
    assert bob.nick.lower() in nicks
    assert bot_client.nick.lower() in nicks


@pytest.mark.asyncio
async def test_privmsg_event_parsing(bot_client: IRCClient, fake_factory) -> None:
    channel = f"#p{_uniq('')[:8]}"
    peer = await fake_factory("p")
    await peer.join(channel)
    await bot_client.join(channel)
    await wait_until(
        lambda: bot_client.state.get_channel(channel) is not None
        and bot_client.isupport.casefold(peer.nick)
        in bot_client.state.get_channel(channel).members,
        timeout=30,
        desc="both in channel",
    )

    events = bot_client._test_events  # type: ignore[attr-defined]
    before = len(events)
    await peer.privmsg(channel, "hello pybot parse")

    await wait_until(
        lambda: any(
            e == "privmsg" and p.get("text") == "hello pybot parse"
            for e, p in events[before:]
        ),
        timeout=15,
        desc="privmsg event",
    )
    priv = next(
        p for e, p in events[before:] if e == "privmsg" and p.get("text") == "hello pybot parse"
    )
    assert priv["nick"] and priv["nick"].lower() == peer.nick.lower()
    assert priv["target"].lower() == channel.lower()


@pytest.mark.asyncio
async def test_mode_op_and_nick_change(bot_client: IRCClient, fake_factory) -> None:
    channel = f"#m{_uniq('')[:8]}"
    # First joiner is typically op on ircu empty channels
    op = await fake_factory("op")
    target = await fake_factory("tg")
    await op.join(channel)
    await bot_client.join(channel)
    await target.join(channel)
    await asyncio.sleep(0.8)

    # op the target
    await op.mode(channel, "+o", target.nick)

    def target_is_op() -> bool:
        ch = bot_client.state.get_channel(channel)
        if not ch:
            return False
        key = bot_client.isupport.casefold(target.nick)
        member = ch.members.get(key)
        return bool(member and "o" in member.prefixes)

    await wait_until(target_is_op, timeout=15, desc="target opped in journal")

    old = target.nick
    new = _uniq("n")
    await target.nick_change(new)

    await wait_until(
        lambda: bot_client.state.get_user(new) is not None
        and bot_client.state.get_user(old) is None,
        timeout=10,
        desc="nick renamed in journal",
    )
    # prefixes should follow rename
    ch = bot_client.state.get_channel(channel)
    assert ch is not None
    member = ch.members.get(bot_client.isupport.casefold(new))
    assert member is not None
    assert "o" in member.prefixes


@pytest.mark.asyncio
async def test_part_and_quit_remove_members(
    bot_client: IRCClient, fake_factory
) -> None:
    channel = f"#q{_uniq('')[:8]}"
    leaver = await fake_factory("lv")
    quitter = await fake_factory("qt")
    await bot_client.join(channel)
    await leaver.join(channel)
    await quitter.join(channel)

    await wait_until(
        lambda: bot_client.state.get_channel(channel) is not None
        and len(bot_client.state.get_channel(channel).members) >= 3,
        timeout=15,
        desc="three members",
    )

    await leaver.part(channel, "leaving")
    await wait_until(
        lambda: bot_client.state.get_channel(channel) is not None
        and bot_client.isupport.casefold(leaver.nick)
        not in bot_client.state.get_channel(channel).members,
        timeout=10,
        desc="part removed",
    )

    qnick = quitter.nick
    await quitter.quit("gone")
    await wait_until(
        lambda: bot_client.state.get_user(qnick) is None,
        timeout=10,
        desc="quit removed user",
    )


@pytest.mark.asyncio
async def test_who_after_names(bot_client: IRCClient, fake_factory) -> None:
    channel = f"#w{_uniq('')[:8]}"
    peer = await fake_factory("w")
    await peer.join(channel)
    await bot_client.join(channel)

    # After 366, client queues WHO; wait for user/host fill-in
    await wait_until(
        lambda: (
            (u := bot_client.state.get_user(peer.nick)) is not None
            and u.user is not None
            and u.host is not None
        ),
        timeout=30,
        desc="WHO filled user/host",
    )
    user = bot_client.state.get_user(peer.nick)
    assert user is not None
    assert user.user == "fake"
    assert user.host
    assert user.realname
