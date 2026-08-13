from __future__ import annotations

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
