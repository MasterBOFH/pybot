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
    api.room_service = svc

    with patch("pybot.modules.medialink.livekit_api.log.warning") as warning, patch.object(
        api, "close", AsyncMock()
    ) as close_mock, patch("pybot.modules.medialink.livekit_api.log.exception") as exception:
        asyncio.run(api.poll_rooms())

    warning.assert_called_once()
    close_mock.assert_awaited_once()
    exception.assert_not_called()
    assert api.room_service is None
