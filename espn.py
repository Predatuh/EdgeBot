"""ESPN enrichment layer. Kalshi tells us WHAT to bet; ESPN adds injuries,
venue and weather context for team sports it covers. Best-effort: if a
match can't be found, the pick still goes out without these extras."""
import datetime as dt
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"
S = requests.Session()
_board_cache = {}


def _board(sport, league):
    key = (sport, league)
    if key in _board_cache:
        return _board_cache[key]
    out = []
    try:
        d = dt.date.today().strftime("%Y%m%d")
        js = S.get(f"{BASE}/{sport}/{league}/scoreboard", params={"dates": d}, timeout=25).json()
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
    return [t for t in "".join(ch if ch.isalnum() or ch == " " else " " for ch in s.lower()).split() if t]


def name_match(kalshi_name, espn_name):
    """Every Kalshi token must be a prefix of some ESPN token.
    'Los Angeles D' matches 'Los Angeles Dodgers' but not 'Los Angeles Angels'."""
    kt, et = _tokens(kalshi_name), _tokens(espn_name)
    if not kt:
        return False
    return all(any(e.startswith(k) for e in et) for k in kt)


def find_game(sport, league, name_a, name_b):
    for g in _board(sport, league):
        n0, n1 = g["teams"][0]["name"], g["teams"][1]["name"]
        if (name_match(name_a, n0) and name_match(name_b, n1)) or \
           (name_match(name_a, n1) and name_match(name_b, n0)):
            return g
    return None


def team_out_count(sport, league, team_id):
    try:
        js = S.get(f"{BASE}/{sport}/{league}/teams/{team_id}",
                   params={"enable": "injuries"}, timeout=20).json()
        n = 0
        for i in (js.get("team") or {}).get("injuries") or []:
            st = i.get("status") or ""
            if isinstance(st, dict):
                st = st.get("type", {}).get("description", "")
            if "out" in str(st).lower():
                n += 1
        return n
    except Exception:
        return 0
