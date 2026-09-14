"""A docked mower flaps between OK_CHARGING and PARKED_TIMER while topping up.

Only the first "charging" notice per docking session reaches the channel; the
rest of the flapping (including the generic debug "activity changed" line) is
suppressed. Leaving the base resets the session.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pybot.modules.gardena.api import GardenaAPI


def _device(activity: str, **extra):
    base = dict(
        id="dev1",
        name="Au pairen",
        type="MOWER",
        model_type="SILENO",
        battery_level=100,
        battery_state="OK",
        rf_link_level=90,
        rf_link_state="ONLINE",
        serial="123",
        operating_hours=10,
        state="OK",
        last_error_code="NO_MESSAGE",
    )
    base.update(extra)
    return SimpleNamespace(activity=activity, **base)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("pybot.modules.gardena.api.adopt_logger", lambda *a, **k: None)
    sent: list[tuple[str, str]] = []
    client = GardenaAPI(
        client_id="x",
        client_secret="y",
        announce=lambda et, text: sent.append((et, text)),
    )
    client.sent = sent  # type: ignore[attr-defined]
    return client


def _replay(api: GardenaAPI, activities: list[str]) -> list[tuple[str, str]]:
    """Seed the cache with the first activity, then feed the rest as updates."""
    api._cache_device_data(_device(activities[0]))
    for act in activities[1:]:
        api.on_device_update(_device(act))
    return list(api.sent)  # type: ignore[attr-defined]


def test_docked_flapping_announces_charging_once(api):
    sent = _replay(
        api,
        [
            "OK_CUTTING",
            "PARKED_TIMER",  # returned to base
            "OK_CHARGING",  # first charge notice: keep
            "PARKED_TIMER",  # top-up cycle: silent
            "OK_CHARGING",
            "PARKED_TIMER",
            "OK_CHARGING",
        ],
    )
    assert [et for et, _ in sent] == ["info", "info"]
    assert "returned to base" in sent[0][1]
    assert "charging up" in sent[1][1]


def test_leaving_base_resets_charge_session(api):
    sent = _replay(
        api,
        [
            "PARKED_TIMER",
            "OK_CHARGING",  # announced
            "PARKED_TIMER",  # silent
            "OK_LEAVING",  # announced, resets session
            "OK_CUTTING",
            "PARKED_TIMER",  # returned to base
            "OK_CHARGING",  # announced again: new docking session
            "PARKED_TIMER",  # silent
        ],
    )
    texts = [t for _, t in sent]
    assert sum("charging up" in t for t in texts) == 2
    assert sum("leaving base" in t for t in texts) == 1
    assert sum("returned to base" in t for t in texts) == 1
    # Only the (legit) OK_LEAVING -> OK_CUTTING debug line, no docked flapping.
    changed = [t for t in texts if "activity changed" in t]
    assert changed == [
        "Device Au pairen activity changed from 'OK_LEAVING' to 'OK_CUTTING'"
    ]


def test_non_docked_transitions_still_reach_debug(api):
    sent = _replay(api, ["OK_CUTTING", "OK_SEARCHING"])
    assert sent == [
        (
            "debug",
            "Device Au pairen activity changed from 'OK_CUTTING' to 'OK_SEARCHING'",
        )
    ]
