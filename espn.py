"""ESPN enrichment layer. Kalshi tells us WHAT to bet; ESPN adds injuries,
venue and weather context for team sports it covers. Best-effort: if a
match can't be found, the pick still goes out without these extras."""
import datetime as dt
import re
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"
S = requests.Session()
_board_cache = {}

# Kalshi abbreviates same-city teams; spell them out so they match ESPN's displayName.
ALIASES = {
    "a's": "athletics", "chicago c": "chicago cubs", "chicago ws": "chicago white sox",
    "new york y": "new york yankees", "new york m": "new york mets",
    "los angeles d": "los angeles dodgers", "los angeles a": "los angeles angels",
    "los angeles r": "los angeles rams", "los angeles c": "los angeles chargers",
    "new york g": "new york giants", "new york j": "new york jets",
}
# Injury statuses that mean the player will not play.
OUT_WORDS = ("out", "injured reserve", "-il", " il", "suspended", "doubtful")


def _board(sport, league):
    key = (sport, league)
    if key in _board_cache:
        return _board_cache[key]
    out = []
    try:
        params = {"dates": dt.date.today().strftime("%Y%m%d"), "limit": 500}
        if league == "college-football":
            params["groups"] = 80            # all FBS games, not just the top 25
        js = S.get(f"{BASE}/{sport}/{league}/scoreboard", params=params, timeout=25).json()
        for ev in js.get("events", []):
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            teams = []
            for c in cs:
                t = c.get("team") or {}
                teams.append({"id": str(t.get("id", "")),
                              "name": t.get("displayName", ""),
                              "home": c.get("homeAway") == "home"})
            ven = comp.get("venue") or {}
            out.append({"teams": teams,
                        "indoor": ven.get("indoor"),
                        "city": (ven.get("address") or {}).get("city"),
                        "venue": ven.get("fullName")})
    except Exception as e:
        print(f"[espn] board {sport}/{league} failed: {e}")
    _board_cache[key] = out
    return out


def _tokens(s):
    s = re.sub(r"\(.*?\)", " ", s).lower().strip()      # 'Miami (FL)' -> 'miami'
    s = ALIASES.get(s, s)
    return [t for t in "".join(ch if ch.isalnum() or ch == " " else " " for ch in s).split() if t]


def name_match(kalshi_name, espn_name):
    """Every Kalshi token must be a prefix of its own (distinct) ESPN token, in order.
    'Los Angeles D' matches 'Los Angeles Dodgers' but not 'Los Angeles Angels'
    (the D has no token left to match once 'Los' and 'Angeles' are used)."""
    kt, et = _tokens(kalshi_name), _tokens(espn_name)
    if not kt:
        return False
    i = 0
    for k in kt:
        while i < len(et) and not et[i].startswith(k):
            i += 1
        if i == len(et):
            return False
        i += 1
    return True


def find_game(sport, league, name_a, name_b):
    for g in _board(sport, league):
        n0, n1 = g["teams"][0]["name"], g["teams"][1]["name"]
        if (name_match(name_a, n0) and name_match(name_b, n1)) or \
           (name_match(name_a, n1) and name_match(name_b, n0)):
            return g
    return None


def team_out_count(sport, league, team_id):
    """Number of players listed as out (Out / IR / IL / suspended / doubtful)."""
    try:
        js = S.get(f"{BASE}/{sport}/{league}/teams/{team_id}",
                   params={"enable": "injuries"}, timeout=20).json()
        n = 0
        for i in (js.get("team") or {}).get("injuries") or []:
            st = i.get("status") or ""
            if isinstance(st, dict):
                st = st.get("type", {}).get("description", "") or st.get("name", "")
            st = " " + str(st).lower()
            if any(w in st for w in OUT_WORDS):
                n += 1
        return n
    except Exception:
        return 0
