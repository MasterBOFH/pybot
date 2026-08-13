"""Gardena Smart System module — mower events + devices/weather commands."""

from __future__ import annotations

from typing import Any

from pybot.core.api import BotAPI
from pybot.core.module import Module, on
from pybot.modules.gardena.api import GardenaAPI
from pybot.modules.gardena.weather import (
    format_weather_lines,
    get_cached_weather,
    update_weather_cache,
    validate_weather_config,
    weather_config,
)


class GardenaModule(Module):
    name = "gardena"

    def __init__(self) -> None:
        super().__init__()
        self.api_client: GardenaAPI | None = None
        self._pending_state: dict[str, Any] = {}
        # (channel name, debug announcements enabled)
        self._channels: list[tuple[str, bool]] = []
        self._cmd_prefix = "~"
        self._weather_enabled = False

    def load_state(self, state: dict[str, Any]) -> None:
        self._pending_state = state or {}

    def get_state(self) -> dict[str, Any]:
        if not self.api_client:
            return {}
        return {
            "device_cache": self.api_client.device_cache.copy(),
            "mowing_start_times": self.api_client.mowing_start_times.copy(),
            "pause_times": self.api_client.pause_times.copy(),
            "location_id": self.api_client.location_id,
        }

    def _apply_state(self, state: dict[str, Any]) -> None:
        if not self.api_client or not state:
            return
        self.api_client.device_cache = state.get("device_cache", {}).copy()
        self.api_client.mowing_start_times = state.get("mowing_start_times", {}).copy()
        self.api_client.pause_times = state.get("pause_times", {}).copy()
        if state.get("location_id") is not None:
            self.api_client.location_id = state["location_id"]

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
        single = cfg.get("channel") or "#dev"
        return [(str(single), False)]

    async def _announce_async(self, event_type: str, text: str) -> None:
        assert self.api is not None
        et = event_type.lower()
        for channel, debug in self._channels:
            if et == "debug" and not debug:
                continue
            if et in ("info", "debug"):
                await self.api.privmsg(channel, text)

    async def setup(self, api: BotAPI) -> None:
        await super().setup(api)
        assert self.api is not None
        cfg = self.config
        bot_cfg = api.get_bot_config()
        self._channels = self._parse_channels(cfg)
        admin = (bot_cfg.get("irc") or {}).get("admin") or {}
        self._cmd_prefix = cfg.get("command_prefix") or admin.get("prefix") or "~"

        client_id = cfg.get("client_id") or ""
        client_secret = cfg.get("client_secret") or ""
        if not client_id or not client_secret:
            self.api.log.error("gardena: client_id / client_secret required")
            return

        owm = weather_config(cfg)
        self._weather_enabled = bool(owm.get("enabled"))

        def announce(event_type: str, text: str) -> None:
            assert self.api is not None
            self.api.schedule(self._announce_async(event_type, text))

        self.api_client = GardenaAPI(
            client_id=client_id,
            client_secret=client_secret,
            announce=announce,
            weather_enabled=self._weather_enabled,
        )
        if self._pending_state:
            self._apply_state(self._pending_state)
            self._pending_state = {}
        self.api_client.start()

        if self._weather_enabled:
            interval = float(owm.get("update_seconds") or 3600)

            async def weather_tick() -> None:
                assert self.api is not None
                loc, cur = await update_weather_cache(self.config)
                if loc:
                    self.api.log.debug("Weather cache updated for %s", loc)
                else:
                    self.api.log.warning("Weather update failed: %s", cur)

            self.api.every(
                interval, weather_tick, name="owm_cache", immediate=True
            )
            self.api.log.info(
                "Weather cache updater every %ss (for Gardena status lines)", interval
            )

        dest = ", ".join(
            f"{ch}{'[debug]' if dbg else ''}" for ch, dbg in self._channels
        )
        self.api.log.info(
            "Gardena started → %s (commands: %sdevices / %sweather)",
            dest,
            self._cmd_prefix,
            self._cmd_prefix,
        )

    async def teardown(self) -> None:
        if self.api_client:
            self.api_client.stop()
            self.api_client = None
        await super().teardown()

    @on("privmsg")
    async def on_privmsg(
        self,
        nick: str | None = None,
        user: str | None = None,
        host: str | None = None,
        target: str | None = None,
        text: str | None = None,
        account: str | None = None,
        **_kwargs: Any,
    ) -> None:
        assert self.api is not None
        text = text or ""
        if not text.startswith(self._cmd_prefix):
            return
        body = text[len(self._cmd_prefix) :].strip()
        parts = body.split()
        if not parts:
            return
        cmd = parts[0].lower()
        reply_to = target if target and target[:1] in ("#", "&") else nick
        if not reply_to:
            return

        if cmd == "weather":
            await self._cmd_weather(reply_to)
        elif cmd == "devices":
            if not self.api.is_admin(
                {
                    "nick": nick,
                    "user": user,
                    "host": host,
                    "account": account,
                }
            ):
                return
            await self._cmd_devices(reply_to)

    async def _cmd_weather(self, reply_to: str) -> None:
        assert self.api is not None
        err = validate_weather_config(self.config)
        if err:
            await self.api.privmsg(reply_to, f"❌ {err}")
            return
        location, current = get_cached_weather()
        if not location:
            location, current = await update_weather_cache(self.config)
            if not location:
                await self.api.privmsg(reply_to, f"❌ {current}")
                return
        for line in format_weather_lines():
            await self.api.privmsg(reply_to, line)

    async def _cmd_devices(self, reply_to: str) -> None:
        assert self.api is not None
        api = self.api_client
        if not api:
            await self.api.privmsg(reply_to, "❌ Gardena module is not loaded.")
            return
        smart = api.get_smart_system()
        if not smart or not api.location_id:
            await self.api.privmsg(reply_to, "❌ Gardena is not connected yet.")
            return
        try:
            location = smart.locations[api.location_id]
            mowers = location.find_device_by_type("MOWER")
        except Exception as exc:
            self.api.log.exception("devices command failed")
            await self.api.privmsg(reply_to, f"❌ Error: {exc}")
            return

        if not mowers:
            await self.api.privmsg(reply_to, "❌ No mowers found.")
            return

        for device in mowers:
            activity_emoji = {
                "ok_cutting": "✂️",
                "ok_cutting_timer_overridden": "✂️",
                "ok_searching": "🔍",
                "ok_leaving": "🚀",
                "ok_charging": "🔌",
                "parked_timer": "🅿️",
                "parked_park_selected": "⏸️",
                "paused": "⏸️",
                "error": "❌",
                "warning": "⚠️",
                "none": "🔴",
            }.get((device.activity or "").lower(), "❓")
            activity_name = (device.activity or "").lower()
            if activity_name == "none":
                activity_display = "Stopped"
            elif activity_name.startswith("ok_"):
                activity_display = activity_name[3:].replace("_", " ").title()
            else:
                activity_display = (device.activity or "?").replace("_", " ").title()

            battery_emoji = {
                "OK": "🔋",
                "CHARGING": "🔌",
                "LOW": "🪫",
                "WARNING": "⚠️",
            }.get(device.battery_state, "⚠️")
            signal_online = device.rf_link_state == "ONLINE"
            signal_emoji = "📶" if signal_online else "📡"
            if signal_online:
                signal_info = f"{signal_emoji} Signal: {device.rf_link_level} (Online)"
            else:
                signal_info = f"{signal_emoji} Signal: Offline"
            state_emoji = {
                "OK": "✅",
                "WARNING": "⚠️",
                "ERROR": "❌",
                "UNKNOWN": "❓",
            }.get(device.state, "❓")

            await self.api.privmsg(
                reply_to,
                f"🤖 *{device.name}* | {activity_emoji} {activity_display} | "
                f"{state_emoji} State: {device.state} | "
                f"{battery_emoji} Battery: {device.battery_level}% ({device.battery_state}) | "
                f"{signal_info} | ⏱️ Operating Hours: {device.operating_hours}",
            )
            if device.last_error_code and device.last_error_code != "N/A":
                await self.api.privmsg(
                    reply_to, f"⚠️ Error: {device.last_error_code}"
                )
