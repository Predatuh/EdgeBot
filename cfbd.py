"""College football injuries from collegefootballdata.com.

ESPN's college-football summary payload has no injuries block (the key is
simply absent), so NFL-style ESPN lookups return nothing for NCAAF. CFBD does
carry them. Needs the free CFBD_API_KEY secret; without it this is a no-op so
the pick still goes out.

Returns the same shape espn.game_injuries does: {team_name: {elo, out}}.
Keyed by CFBD team name, not ESPN id — college matching is by name.
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import espn

CFBD = "https://api.collegefootballdata.com"
UA = {"User-Agent": "EdgeBot/2"}

_cache = {}


def _key():
    return os.environ.get("CFBD_API_KEY", "").strip()


def available():
    return bool(_key())


def _get(endpoint, params, timeout=25):
    key = _key()
    if not key:
        return None
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(
        f"{CFBD}{endpoint}?{q}",
        headers=dict(UA, Authorization=f"Bearer {key}"),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        print(f"[cfbd] {endpoint}: {type(e).__name__}: {str(e)[:80]}")
        return None


def _season_year(day=None):
    day = day or dt.date.today()
    return day.year if day.month >= 7 else day.year - 1


def _week(day=None):
    """CFBD week number for this date.

    Prefer the calendar endpoint (week 0 / 1 / 2 are not a uniform 7 days from
    Aug 15). Fall back to counting weeks from late August if the calendar is
    missing — an off-by-one is recovered in injuries() by trying adjacent weeks.
    """
    day = day or dt.date.today()
    year = _season_year(day)
    cal = _get("/calendar", {"year": year})
    if isinstance(cal, list):
        for w in cal:
            st = str(w.get("seasonType") or "regular").lower()
            if st not in ("regular", "postseason", "both", ""):
                continue
            start_s = str(w.get("firstGameStart") or w.get("startDate") or "")[:10]
            end_s = str(w.get("lastGameStart") or w.get("endDate") or "")[:10]
            if len(start_s) < 10 or len(end_s) < 10:
                continue
            try:
                start = dt.date.fromisoformat(start_s)
                end = dt.date.fromisoformat(end_s)
            except ValueError:
                continue
            if start <= day <= end + dt.timedelta(days=2):
                try:
                    return int(w.get("week") or 0)
                except (TypeError, ValueError):
                    continue
    start = dt.date(year, 8, 24)
    return max(0, (day - start).days // 7)


def _player(row):
    """CFBD has shipped a few shapes. Pull a name / position / status from any of them."""
    athlete = row.get("athlete") or {}
    name = (row.get("player") or row.get("athleteName") or row.get("name")
            or athlete.get("name") or athlete.get("displayName") or "")
    pos = row.get("position") or athlete.get("position") or ""
    if isinstance(pos, dict):
        pos = pos.get("abbreviation") or pos.get("name") or ""
    status = (row.get("status") or row.get("classification") or row.get("injury") or "")
    if isinstance(status, dict):
        status = status.get("status") or status.get("type") or ""
    team = row.get("team") or row.get("teamName") or ""
    if isinstance(team, dict):
        team = team.get("school") or team.get("name") or ""
    comment = row.get("comment") or ""
    if not comment and isinstance(row.get("details"), dict):
        comment = row["details"].get("comment") or row["details"].get("status") or ""
    return {
        "name": str(name or "").strip(),
        "pos": str(pos or "").upper().strip(),
        "status": str(status or "").strip(),
        "team": str(team or "").strip(),
        "note": str(comment or ""),
    }


def injuries(year=None, week=None):
    """{team: {elo, out: [records]}} for this week, or {} if no key / no data.

    Cached per (year, week) so a 12-game Saturday board is one HTTP call.
    """
    year = year or _season_year()
    week = _week() if week is None else week
    cache_key = (year, week)
    if cache_key in _cache:
        return _cache[cache_key]

    def fetch(w):
        return _get("/injuries", {"year": year, "week": w})

    rows = fetch(week)
    used = week
    if not (isinstance(rows, list) and rows):
        for w in (week - 1, week + 1):
            if w < 0:
                continue
            got = fetch(w)
            if isinstance(got, list) and got:
                rows, used = got, w
                break
    if not (isinstance(rows, list) and rows):
        # some seasons expose the list without a week filter
        rows = _get("/injuries", {"year": year})
    if not isinstance(rows, list):
        _cache[cache_key] = {}
        return {}
    weights = espn.POSITION_ELO.get("football", {})
    default = espn.DEFAULT_ELO.get("football", 5)
    by_team = {}
    for row in rows:
        rec = _player(row)
        if not rec["team"] or not rec["name"]:
            continue
        w = espn._status_weight(rec["status"])
        if not w:
            continue
        val = weights.get(rec["pos"], default) * w
        rec["elo"] = round(val, 1)
        slot = by_team.setdefault(rec["team"], {"elo": 0.0, "out": []})
        slot["elo"] += val
        slot["out"].append(rec)
    for slot in by_team.values():
        slot["elo"] = round(min(slot["elo"], espn.MAX_INJURY_ELO), 1)
        slot["out"].sort(key=lambda r: -r["elo"])
    _cache[cache_key] = by_team
    print(f"[cfbd] injuries {year} w{used}: {len(by_team)} teams, "
          f"{sum(len(v['out']) for v in by_team.values())} listed")
    return by_team


def for_teams(name_a, name_b):
    """Injury info for two Kalshi-named sides, or (None, None)."""
    table = injuries()
    if not table:
        return None, None

    def find(name):
        for team, info in table.items():
            if espn.name_match(name, team) or espn.name_match(team, name):
                return info
            if name.lower() == team.lower():
                return info
        return None

    return find(name_a), find(name_b)
