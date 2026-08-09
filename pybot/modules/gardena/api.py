"""Gardena Smart System API client (background thread + websocket)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from pybot.modules.gardena.weather import format_weather_snippet

log = logging.getLogger("pybot.modules.gardena")


def _patch_websockets_closed() -> None:
    """py-smart-gardena<=1.3.17 uses websocket.closed; websockets 14+ removed it.

    Upstream fix is merged but unreleased; shim ClientConnection.closed via state.
    """
    try:
        from websockets.asyncio.client import ClientConnection
        from websockets.protocol import State
    except ImportError:
        return
    if hasattr(ClientConnection, "closed"):
        return

    def _closed(self: Any) -> bool:
        return self.state is State.CLOSED

    ClientConnection.closed = property(_closed)  # type: ignore[attr-defined]
    log.debug("Patched websockets.ClientConnection.closed for py-smart-gardena")


def _smart_system_cls():
    _patch_websockets_closed()
    from gardena.smart_system import SmartSystem

    return SmartSystem


class GardenaAPI:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        announce: Callable[[str, str], None],
        weather_enabled: bool = False,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        # announce(event_type, message) — event_type is "info" or "debug"
        self._announce = announce
        self.weather_enabled = weather_enabled

        self.smart_system = None
        self.thread: threading.Thread | None = None
        self.device_cache: dict[str, dict[str, Any]] = {}
        self.mowing_start_times: dict[str, float] = {}
        self.pause_times: dict[str, float] = {}
        self.location_id: str | None = None
        self.running = False
        self._stop_event = threading.Event()

        # Keep SDK / websocket noise on our module logger at DEBUG
        for name in ("gardena.smart_system", "websockets"):
            gl = logging.getLogger(name)
            gl.handlers.clear()
            gl.setLevel(logging.DEBUG)
            gl.propagate = True

        log.debug("GardenaAPI initialized")

    def _cache_device_data(self, device: Any) -> None:
        self.device_cache[device.id] = {
            "name": device.name,
            "id": device.id,
            "type": device.type,
            "model_type": device.model_type,
            "battery_level": device.battery_level,
            "battery_state": device.battery_state,
            "rf_link_level": device.rf_link_level,
            "rf_link_state": device.rf_link_state,
            "serial": device.serial,
            "activity": device.activity,
            "operating_hours": device.operating_hours,
            "state": device.state,
            "last_error_code": device.last_error_code,
        }

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            log.debug("Stopping existing Gardena thread before restart")
            self.stop()
            time.sleep(1)
        self._stop_event.clear()
        self.running = True
        self.thread = threading.Thread(
            target=self._thread_entry, daemon=True, name="GardenaModule"
        )
        self.thread.start()
        log.debug("Gardena thread started")

    def stop(self) -> None:
        if not self.thread or not self.thread.is_alive():
            return
        log.debug("Stopping Gardena thread…")
        self.running = False
        self._stop_event.set()
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            log.warning("Gardena thread did not stop gracefully")
        if self.smart_system:
            try:
                if getattr(self.smart_system, "ws", None):
                    asyncio.run(self.smart_system.ws.close())
            except Exception:
                log.exception("Error cleaning up smart system")
            finally:
                self.smart_system = None
        self.thread = None

    def _thread_entry(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("Error in Gardena thread")
        finally:
            self.running = False

    async def _main(self) -> None:
        try:
            SmartSystem = _smart_system_cls()
            self.smart_system = SmartSystem(
                client_id=self.client_id,
                client_secret=self.client_secret,
                level=logging.DEBUG,
            )
            await self.smart_system.authenticate()
            await self.smart_system.update_locations()

            for location in self.smart_system.locations.values():
                await self.smart_system.update_devices(location)
                log.info("Location: %s (%s)", location.name, location.id)
                self.location_id = location.id
                for device in location.devices.values():
                    device.add_callback(self.on_device_update)
                    self._cache_device_data(device)
                    log.debug("Device: %s", device)

            while self.running and not self._stop_event.is_set():
                try:
                    if not self.smart_system or not self.smart_system.locations:
                        log.error("Smart system or locations not available")
                        break
                    location = self.smart_system.locations.get(self.location_id)
                    if not location:
                        log.error("Location %s not found", self.location_id)
                        break
                    await self.smart_system.start_ws(location)
                except Exception as exc:
                    if self.running and not self._stop_event.is_set():
                        log.error("Websocket error: %s", exc)
                        await asyncio.sleep(5)
                    else:
                        break
        except Exception:
            log.exception("Error in Gardena main loop")
        finally:
            self.running = False
            if self.smart_system:
                try:
                    if getattr(self.smart_system, "ws", None):
                        await self.smart_system.ws.close()
                except Exception:
                    log.exception("Error closing websocket")
                self.smart_system = None

    def _weather_suffix(self) -> str:
        if not self.weather_enabled:
            return ""
        return format_weather_snippet()

    def on_device_update(self, device: Any) -> None:
        old_activity = self.device_cache.get(device.id, {}).get("activity")
        old_state = self.device_cache.get(device.id, {}).get("state")
        old_error = self.device_cache.get(device.id, {}).get("last_error_code")
        self._cache_device_data(device)

        if old_activity != device.activity:
            weather_info = self._weather_suffix()
            activity_lower = (device.activity or "").lower()
            old_activity_lower = (old_activity or "").lower()

            if (
                activity_lower == "parked_timer"
                and old_activity_lower
                not in ("", "none", "ok_charging", "parked_park_selected")
            ):
                duration_msg = ""
                if device.id in self.mowing_start_times:
                    duration = time.time() - self.mowing_start_times.pop(device.id)
                    hours, minutes = int(duration // 3600), int((duration % 3600) // 60)
                    duration_msg = f" | Mowing duration: {hours}h {minutes}m"
                self._announce(
                    "info",
                    f"🏠 {device.name} has finished mowing and returned to base!"
                    f"{duration_msg}{weather_info}",
                )
            elif activity_lower == "ok_leaving" and old_activity_lower != "ok_cutting_timer_overridden":
                self.mowing_start_times[device.id] = time.time()
                self._announce(
                    "info",
                    f"🚀 {device.name} is leaving base to start mowing!{weather_info}",
                )
            elif (
                activity_lower == "ok_cutting_timer_overridden"
                and old_activity_lower != "ok_leaving"
            ):
                self._announce(
                    "info",
                    f"✂️ {device.name} has been manually deployed - mowing in progress!"
                    f"{weather_info}",
                )
            elif activity_lower == "paused" and old_activity_lower not in ("", "none"):
                self.pause_times[device.id] = time.time()
                self._announce(
                    "info",
                    f"⏸️ {device.name} has been paused. Take a break, little mower!"
                    f"{weather_info}",
                )
            elif activity_lower == "ok_cutting" and old_activity_lower in (
                "paused",
                "none",
            ):
                resumed_from = "paused" if old_activity_lower == "paused" else "stopped"
                pause_duration_msg = ""
                pause_start = self.pause_times.pop(device.id, None)
                if pause_start:
                    duration = time.time() - pause_start
                    hours, minutes = int(duration // 3600), int((duration % 3600) // 60)
                    if duration > 5:
                        pause_duration_msg = f" ({resumed_from} for {hours}h {minutes}m)"
                self._announce(
                    "info",
                    f"▶️ {device.name} has resumed mowing after being {resumed_from}"
                    f"{pause_duration_msg}!{weather_info}",
                )
            elif activity_lower == "ok_charging":
                duration_msg = ""
                if device.id in self.mowing_start_times:
                    duration = time.time() - self.mowing_start_times.pop(device.id)
                    hours, minutes = int(duration // 3600), int((duration % 3600) // 60)
                    duration_msg = f" | Mowed for {hours}h {minutes}m before charging"
                self._announce(
                    "info",
                    f"🔌 {device.name} is charging up before heading out again!"
                    f"{duration_msg}{weather_info}",
                )
            else:
                self._announce(
                    "debug",
                    f"Device {device.name} activity changed from "
                    f"'{old_activity}' to '{device.activity}'",
                )

        if old_state != device.state:
            self._announce(
                "debug",
                f"Device {device.name} state changed from '{old_state}' to '{device.state}'",
            )

        if old_error != device.last_error_code:
            error_lower = (device.last_error_code or "").lower()
            if error_lower == "alarm_mower_lifted":
                self._announce(
                    "info",
                    f"🚨 {device.name} has been LIFTED while mowing! 🕵️‍♂️",
                )
            else:
                self._announce(
                    "debug",
                    f"Device {device.name} error code changed from "
                    f"'{old_error}' to '{device.last_error_code}'",
                )

    def get_smart_system(self) -> Any:
        return self.smart_system

    def get_device_cache(self) -> dict[str, dict[str, Any]]:
        return self.device_cache
