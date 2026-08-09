"""OpenWeatherMap cache for Gardena status lines and ~weather."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("pybot.modules.gardena.weather")

_weather_cache: dict[str, Any] = {
    "data": None,
    "last_update": 0.0,
    "lock": threading.Lock(),
}


def weather_config(module_config: dict[str, Any]) -> dict[str, Any]:
    return dict(module_config.get("weather") or {})


def validate_weather_config(module_config: dict[str, Any]) -> str | None:
    cfg = weather_config(module_config)
    if not cfg.get("enabled", False):
        return "Weather disabled (set modules.gardena.weather.enabled: true)"
    if not cfg.get("api_key"):
        return "modules.gardena.weather.api_key not set"
    if not cfg.get("location"):
        return "modules.gardena.weather.location not set"
    if cfg.get("latitude") is None or cfg.get("longitude") is None:
        return "modules.gardena.weather.latitude / longitude not set"
    return None


async def fetch_weather(module_config: dict[str, Any]) -> tuple[Any, Any]:
    """Return (location_name, current) or (None, error_str)."""
    from pyopenweathermap import create_owm_client

    cfg = weather_config(module_config)
    location = cfg.get("location")
    try:
        owm = create_owm_client(api_key=cfg["api_key"], api_type="current")
        weather = await owm.get_weather(float(cfg["latitude"]), float(cfg["longitude"]))
        return location, weather.current
    except Exception as exc:
        log.exception("Weather fetch failed")
        return None, f"Error getting weather for '{location}': {exc}"


def get_cached_weather() -> tuple[Any, Any]:
    with _weather_cache["lock"]:
        if _weather_cache["data"] is not None:
            return _weather_cache["data"]
    return None, "No weather data available"


async def update_weather_cache(module_config: dict[str, Any]) -> tuple[Any, Any]:
    location, current = await fetch_weather(module_config)
    if location is not None:
        with _weather_cache["lock"]:
            _weather_cache["data"] = (location, current)
            _weather_cache["last_update"] = time.time()
    return location, current


def get_weather_condition_emojis() -> dict[str, str]:
    return {
        "Clear": "☀️",
        "Clouds": "☁️",
        "Rain": "🌧️",
        "Drizzle": "🌦️",
        "Thunderstorm": "⛈️",
        "Snow": "🌨️",
        "Mist": "🌫️",
        "Fog": "🌫️",
        "Haze": "🌫️",
        "Smoke": "💨",
        "Dust": "💨",
        "Sand": "💨",
        "Ash": "💨",
        "Squall": "💨",
        "Tornado": "🌪️",
    }


def get_wind_direction_emoji(wind_direction: float) -> str:
    wind_emoji = {
        (0, 22.5): "⬆️",
        (22.5, 67.5): "↗️",
        (67.5, 112.5): "➡️",
        (112.5, 157.5): "↘️",
        (157.5, 202.5): "⬇️",
        (202.5, 247.5): "↙️",
        (247.5, 292.5): "⬅️",
        (292.5, 337.5): "↖️",
        (337.5, 360): "⬆️",
    }
    for (start, end), emoji in wind_emoji.items():
        if start <= wind_direction < end:
            return emoji
    return "⬆️"


def format_weather_snippet() -> str:
    """Short suffix for Gardena status lines, or empty string."""
    location, current = get_cached_weather()
    if not location or isinstance(current, str) or current is None:
        return ""
    try:
        condition = current.condition.main
        emoji = get_weather_condition_emojis().get(condition, "❓")
        temp = round(current.temperature)
        wind = get_wind_direction_emoji(current.wind_bearing)
        return (
            f" | Current weather: {emoji} {temp}°C | "
            f"💨 {wind} {round(current.wind_speed)} m/s | "
            f"💧 {current.humidity}%"
        )
    except Exception:
        log.debug("Could not format weather snippet", exc_info=True)
        return ""


def format_weather_lines() -> list[str]:
    location, current = get_cached_weather()
    if not location or isinstance(current, str) or current is None:
        return []
    condition = current.condition.main
    emoji = get_weather_condition_emojis().get(condition, "❓")
    temp = round(current.temperature)
    feels = round(current.feels_like)
    wind = get_wind_direction_emoji(current.wind_bearing)
    return [
        f"*{location}* | {emoji} {condition} | 🌡️ {temp}°C (feels like {feels}°C)",
        (
            f"💨 Wind: {wind} {round(current.wind_speed)} m/s | "
            f"💧 Humidity: {current.humidity}% | 🌫️ Pressure: {current.pressure} hPa"
        ),
        (
            f"☁️ Cloud coverage: {current.cloud_coverage}% | "
            f"👁️ Visibility: {current.visibility / 1000:.1f} km"
        ),
    ]
