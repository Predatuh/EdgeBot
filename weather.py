"""Weather for outdoor games via Open-Meteo (free, no API key).

Prefers the hourly forecast at kickoff, not "current" at run time — a 7am
card should not flag a 1pm kick as 35 mph wind because of the morning gust.
Falls back to current only when there is no kickoff time or the hourly
grid does not reach it. Used as a flag on the pick card and a small Elo
nudge for extreme conditions.
"""
import datetime as dt

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


def _parse_when(when):
    """ISO kickoff -> aware UTC datetime, or None."""
    if not when:
        return None
    if isinstance(when, dt.datetime):
        t = when
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    s = str(when).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _closest_hour(times, temps, winds, precips, probs, target):
    """Pick the hourly slot nearest kickoff. times are 'YYYY-MM-DDTHH:MM' local."""
    best_i, best_dt = None, None
    for i, stamp in enumerate(times or []):
        try:
            slot = dt.datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=target.tzinfo)
        if best_dt is None or abs((slot - target).total_seconds()) < abs((best_dt - target).total_seconds()):
            best_i, best_dt = i, slot
    if best_i is None:
        return None
    # more than 3 hours off means the grid did not cover this kick
    if abs((best_dt - target).total_seconds()) > 3 * 3600:
        return None
    def at(arr):
        try:
            return arr[best_i]
        except (TypeError, IndexError):
            return None
    return {
        "temp_f": at(temps),
        "wind_mph": at(winds),
        "precip": at(precips),
        "precip_prob": at(probs),
        "at": times[best_i],
        "kickoff": True,
    }


def forecast(city, when=None):
    """dict(temp_f, wind_mph, precip, ...) at kickoff if `when` is known, else now.

    `when` is an ISO datetime (Kalshi close / ESPN event date). None when the
    city cannot be geocoded or the feed fails — the pick still goes out.
    """
    co = city_coords(city)
    if not co:
        return None
    target = _parse_when(when)
    params = {
        "latitude": co[0], "longitude": co[1],
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "GMT",
    }
    if target:
        params["hourly"] = "temperature_2m,wind_speed_10m,precipitation,precipitation_probability"
        params["forecast_days"] = 16
    else:
        params["current"] = "temperature_2m,wind_speed_10m,precipitation"
    try:
        js = requests.get("https://api.open-meteo.com/v1/forecast",
                          params=params, timeout=15).json()
    except Exception:
        return None
    if target:
        hourly = js.get("hourly") or {}
        hit = _closest_hour(hourly.get("time"), hourly.get("temperature_2m"),
                            hourly.get("wind_speed_10m"), hourly.get("precipitation"),
                            hourly.get("precipitation_probability"), target)
        if hit:
            return hit
        # hourly missed; try current as a last resort so a same-day game still
        # has *something* rather than a blank
    cur = js.get("current") or {}
    if not cur and not target:
        return None
    if not cur:
        return None
    return {
        "temp_f": cur.get("temperature_2m"),
        "wind_mph": cur.get("wind_speed_10m"),
        "precip": cur.get("precipitation"),
        "kickoff": False,
    }


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
    elif (w.get("precip_prob") or 0) >= 40:
        bits.append(f'{round(w["precip_prob"])}% rain')
    when = "at kickoff" if w.get("kickoff") else "now"
    return ((" ".join(bits) + " " + when) if bits else "").strip()


def extreme(w):
    """True when conditions are severe enough to distrust the model a bit."""
    if not w:
        return False
    return (w.get("wind_mph") or 0) >= 20 or (w.get("temp_f") or 70) <= 25 \
        or (w.get("temp_f") or 70) >= 95 or (w.get("precip") or 0) >= 2
