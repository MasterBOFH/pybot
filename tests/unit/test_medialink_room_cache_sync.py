from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, Mock, patch

from pybot.modules.medialink.livekit_api import LiveKitAPI


def _api() -> LiveKitAPI:
    return LiveKitAPI(
        api_key="k",
        api_secret="s",
        server_url="https://lk.example",
    )


def test_note_room_started_populates_cache() -> None:
    api = _api()

    api.note_room_started(
        "#iron-dev",
        {
            "name": "#iron-dev",
            "sid": "RM_123",
            "creation_time": "2026-08-13T10:00:00Z",
            "num_participants": 1,
        },
    )

    room = api.room_cache["#iron-dev"]
    assert room["name"] == "#iron-dev"
    assert room["sid"] == "RM_123"
    assert room["creation_time"] == "2026-08-13T10:00:00Z"
    assert room["num_participants"] == 1
    assert room["last_seen"] is not None


def test_note_room_finished_clears_room_and_participants() -> None:
    api = _api()
    api.note_room_started("#iron-dev", {"name": "#iron-dev"})
    api.note_participant("#iron-dev", "MrIron", "MrIron", joined=True)

    assert "#iron-dev" in api.room_cache
    assert "#iron-dev" in api.participant_cache

    api.note_room_finished("#iron-dev")

    assert "#iron-dev" not in api.room_cache
    assert "#iron-dev" not in api.participant_cache


def test_lifecycle_dedupe_same_event_room_sid() -> None:
    api = _api()

    assert api.should_announce_lifecycle("room_started", "#iron-dev", "RM_1")
    assert not api.should_announce_lifecycle("room_started", "#iron-dev", "RM_1")


def test_lifecycle_dedupe_allows_new_sid() -> None:
    api = _api()

    assert api.should_announce_lifecycle("room_started", "#iron-dev", "RM_1")
    assert api.should_announce_lifecycle("room_started", "#iron-dev", "RM_2")


def test_poll_rooms_handles_backend_unavailable_gracefully() -> None:
    import asyncio

    livekit_module = types.ModuleType("livekit")
    livekit_api_module = types.ModuleType("livekit.api")

    class FakeListRoomsRequest:
        pass

    livekit_api_module.ListRoomsRequest = FakeListRoomsRequest
    livekit_module.api = livekit_api_module
    sys.modules["livekit"] = livekit_module
    sys.modules["livekit.api"] = livekit_api_module

    api = _api()
    svc = Mock()
    svc.room.list_rooms = AsyncMock(side_effect=RuntimeError("backend unavailable"))
    svc.aclose = AsyncMock()
    api.room_service = svc

    with patch("pybot.modules.medialink.livekit_api.log.warning") as warning, patch(
        "pybot.modules.medialink.livekit_api.log.exception"
    ) as exception:
        asyncio.run(api.poll_rooms())

    warning.assert_called_once()
    svc.aclose.assert_awaited_once()  # the dropped client's HTTP session is closed
    exception.assert_not_called()
    assert api.room_service is None


def _fake_livekit_api(list_rooms: AsyncMock) -> Mock:
    livekit_module = types.ModuleType("livekit")
    livekit_api_module = types.ModuleType("livekit.api")

    class FakeListRoomsRequest:
        pass

    svc = Mock()
    svc.room.list_rooms = list_rooms
    svc.aclose = AsyncMock()
    livekit_api_module.ListRoomsRequest = FakeListRoomsRequest
    livekit_api_module.LiveKitAPI = Mock(return_value=svc)
    livekit_module.api = livekit_api_module
    sys.modules["livekit"] = livekit_module
    sys.modules["livekit.api"] = livekit_api_module
    return svc


def test_outage_reported_once_then_recovery() -> None:
    import asyncio

    list_rooms = AsyncMock(return_value=Mock(rooms=[]))
    svc = _fake_livekit_api(list_rooms)
    issues: list[str] = []
    api = LiveKitAPI(
        api_key="k",
        api_secret="s",
        server_url="https://lk.example",
        report_issue=issues.append,
    )

    async def run() -> None:
        await api.connect()
        assert issues == []  # healthy start is not an issue
        list_rooms.side_effect = RuntimeError("certificate has expired")
        await api.poll_rooms()  # drops the connection
        await api.poll_rooms()  # reconnect attempt fails again
        await api.poll_rooms()
        list_rooms.side_effect = None
        await api.poll_rooms()  # reconnects

    with patch("pybot.modules.medialink.livekit_api.log.warning"):
        asyncio.run(run())

    assert len(issues) == 2
    assert issues[0].startswith("⚠️ Lost connection to LiveKit (https://lk.example)")
    assert "certificate has expired" in issues[0]
    assert issues[1].startswith("✅ LiveKit connection restored")
    assert api.healthy is True
    # Every client that was dropped (1 poll failure + 2 failed reconnects) was closed.
    assert svc.aclose.await_count == 3


def test_unreachable_at_startup_is_reported() -> None:
    import asyncio

    svc = _fake_livekit_api(AsyncMock(side_effect=RuntimeError("x" * 1000)))
    issues: list[str] = []
    api = LiveKitAPI(
        api_key="k",
        api_secret="s",
        server_url="https://lk.example",
        report_issue=issues.append,
    )

    with patch("pybot.modules.medialink.livekit_api.log.warning"):
        asyncio.run(api.connect())

    assert len(issues) == 1
    assert issues[0].startswith("⚠️ Cannot connect to LiveKit")
    assert len(issues[0]) < 400
    assert api.healthy is False
    svc.aclose.assert_awaited_once()
    assert api.room_service is None


def test_module_holds_issues_until_in_debug_channels() -> None:
    import asyncio

    from pybot.modules.medialink.module import MedialinkModule

    mod = MedialinkModule()
    mod._channels = [("#room", False), ("#ops", True)]
    joined: set[str] = set()
    bot_api = Mock()
    bot_api.privmsg = AsyncMock()
    bot_api.get_channel = Mock(side_effect=lambda name: object() if name in joined else None)
    bot_api.get_own_nick = Mock(return_value="bot")
    bot_api.nicks_equal = Mock(side_effect=lambda a, b: a.lower() == b.lower())
    mod.api = bot_api

    async def run() -> None:
        await mod._report_issue("⚠️ down")
        bot_api.privmsg.assert_not_awaited()
        joined.add("#ops")
        await mod.on_join(nick="Bot", channel="#OPS")

    asyncio.run(run())

    # Only debug: true channels get issue reports.
    bot_api.privmsg.assert_awaited_once_with("#ops", "⚠️ down")
    assert not mod._pending_issues["#ops"]
    assert "#room" not in mod._pending_issues
