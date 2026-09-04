"""Weather for outdoor games via Open-Meteo (free, no API key).
Geocodes the venue city, pulls temp/wind/precip. Used as a flag on the pick
card ('35 mph wind' matters for totals and kickers) and a small Elo nudge for
extreme conditions."""
import requests

_geo_cache = {}


def city_coords(city):
    if not city:
        return None
    if city in _geo_cache:
        return _geo_cache[city]
    try:
        js = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1}, timeout=15).json()
        res = (js.get("results") or [None])[0]
        out = (res["latitude"], res["longitude"]) if res else None
    except Exception:
        out = None
    _geo_cache[city] = out
    return out


def forecast(city):
    """Return dict(temp_f, wind_mph, precip_prob) for today at the city, or None."""
    co = city_coords(city)
    if not co:
        return None
    try:
        js = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": co[0], "longitude": co[1],
                "current": "temperature_2m,wind_speed_10m,precipitation",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            }, timeout=15).json()
        cur = js.get("current") or {}
        return {
            "temp_f": cur.get("temperature_2m"),
            "wind_mph": cur.get("wind_speed_10m"),
            "precip": cur.get("precipitation"),
        }
    except Exception:
        return None


def describe(w):
    if not w:
        return ""
    bits = []
    if w.get("temp_f") is not None:
        bits.append(f'{round(w["temp_f"])}°F')
    if w.get("wind_mph") is not None:
        bits.append(f'wind {round(w["wind_mph"])}mph')
    if (w.get("precip") or 0) > 0:
        bits.append("precip")
    return " ".join(bits)


def extreme(w):
    """True when conditions are severe enough to distrust the model a bit."""
    if not w:
        return False
    return (w.get("wind_mph") or 0) >= 20 or (w.get("temp_f") or 70) <= 25 \
        or (w.get("temp_f") or 70) >= 95 or (w.get("precip") or 0) >= 2
