"""
Jarvis Protocol — Weather Tool
Uses wttr.in (free, no API key required) for weather data.
"""
import json
import logging
import urllib.request
import urllib.error
from functools import lru_cache
from datetime import datetime

logger = logging.getLogger("jarvis.tools.weather")

# Default location (auto-detected by IP if not set)
DEFAULT_LOCATION = ""

_cache = {}
_cache_ttl = 600  # 10 minutes


def _fetch_weather(location: str) -> dict | None:
    """Fetch weather JSON from wttr.in with caching."""
    cache_key = location.lower().strip()
    now = datetime.now().timestamp()

    # Check cache
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if now - ts < _cache_ttl:
            return data

    url = f"https://wttr.in/{urllib.request.quote(location)}?format=j1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            _cache[cache_key] = (data, now)
            return data
    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return None


def get_current_weather(location: str = "") -> dict:
    """Get current weather conditions."""
    loc = location or DEFAULT_LOCATION
    data = _fetch_weather(loc)
    if not data:
        return {"error": "Could not fetch weather data. Check your internet connection."}

    current = data.get("current_condition", [{}])[0]
    area = data.get("nearest_area", [{}])[0]
    area_name = area.get("areaName", [{}])[0].get("value", "Unknown")
    country = area.get("country", [{}])[0].get("value", "")

    return {
        "location": f"{area_name}, {country}",
        "temp_c": current.get("temp_C", "?"),
        "temp_f": current.get("temp_F", "?"),
        "feels_like_c": current.get("FeelsLikeC", "?"),
        "condition": current.get("weatherDesc", [{}])[0].get("value", "Unknown"),
        "humidity": current.get("humidity", "?"),
        "wind_kph": current.get("windspeedKmph", "?"),
        "wind_dir": current.get("winddir16Point", "?"),
        "uv_index": current.get("uvIndex", "?"),
        "visibility_km": current.get("visibility", "?"),
        "pressure_mb": current.get("pressure", "?"),
    }


def get_forecast(location: str = "", days: int = 3) -> dict:
    """Get weather forecast for upcoming days."""
    loc = location or DEFAULT_LOCATION
    data = _fetch_weather(loc)
    if not data:
        return {"error": "Could not fetch weather data."}

    area = data.get("nearest_area", [{}])[0]
    area_name = area.get("areaName", [{}])[0].get("value", "Unknown")

    forecast = []
    for day in data.get("weather", [])[:days]:
        forecast.append({
            "date": day.get("date", ""),
            "max_c": day.get("maxtempC", "?"),
            "min_c": day.get("mintempC", "?"),
            "condition": day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "")
                if len(day.get("hourly", [])) > 4 else "Unknown",
            "chance_rain": day.get("hourly", [{}])[4].get("chanceofrain", "0")
                if len(day.get("hourly", [])) > 4 else "0",
            "sunrise": day.get("astronomy", [{}])[0].get("sunrise", "")
                if day.get("astronomy") else "",
            "sunset": day.get("astronomy", [{}])[0].get("sunset", "")
                if day.get("astronomy") else "",
        })

    return {
        "location": area_name,
        "forecast": forecast,
    }


def get_weather_summary() -> dict:
    """Get a quick weather summary for the dashboard."""
    data = _fetch_weather(DEFAULT_LOCATION)
    if not data:
        return {"available": False}

    current = data.get("current_condition", [{}])[0]
    area = data.get("nearest_area", [{}])[0]
    area_name = area.get("areaName", [{}])[0].get("value", "Unknown")

    # Map condition descriptions to emoji
    desc = current.get("weatherDesc", [{}])[0].get("value", "").lower()
    icon = "☀️"
    if "cloud" in desc or "overcast" in desc:
        icon = "☁️"
    elif "rain" in desc or "drizzle" in desc:
        icon = "🌧️"
    elif "snow" in desc:
        icon = "❄️"
    elif "thunder" in desc or "storm" in desc:
        icon = "⛈️"
    elif "fog" in desc or "mist" in desc:
        icon = "🌫️"
    elif "clear" in desc or "sunny" in desc:
        icon = "☀️"
    elif "partly" in desc:
        icon = "⛅"

    today_weather = data.get("weather", [{}])[0] if data.get("weather") else {}

    return {
        "available": True,
        "location": area_name,
        "temp_c": current.get("temp_C", "?"),
        "feels_like_c": current.get("FeelsLikeC", "?"),
        "condition": current.get("weatherDesc", [{}])[0].get("value", "Unknown"),
        "icon": icon,
        "humidity": current.get("humidity", "?"),
        "wind_kph": current.get("windspeedKmph", "?"),
        "high_c": today_weather.get("maxtempC", "?"),
        "low_c": today_weather.get("mintempC", "?"),
    }
