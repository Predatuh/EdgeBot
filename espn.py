"""ESPN public JSON API adapter.
One uniform API covers NFL, college football, MLB, soccer, tennis and cricket:
  https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
Tennis nests matches under events[].groupings[].competitions[]; team sports use
events[].competitions[]. Both are normalized here into one shape.
"""
import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports"
S = requests.Session()
# NOTE: default requests headers work; some custom User-Agents get 403'd by ESPN's edge.


def scoreboard(sport, league, date=None):
    url = f"{BASE}/{sport}/{league}/scoreboard"
    params = {"dates": date} if date else {}
    r = S.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def _num(v):
    try:
        if isinstance(v, dict):
            v = v.get("value") or v.get("displayValue")
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _competitor(x):
    ent = x.get("team") or x.get("athlete") or {}
    if "linescores" in x and x.get("athlete"):          # tennis: score = sets won
        score = sum(1 for ls in x["linescores"] if ls.get("winner"))
    else:
        score = _num(x.get("score"))
    return {
        "id": str(ent.get("id") or x.get("id") or ""),
        "name": ent.get("displayName") or ent.get("shortDisplayName") or "?",
        "home": x.get("homeAway") == "home",
        "score": score,
        "winner": bool(x.get("winner", False)),
    }


def _ml(o, side):
    """Moneyline from the new nested format, falling back to legacy fields."""
    try:
        node = o["moneyline"][side]
        v = (node.get("close") or {}).get("odds") or (node.get("open") or {}).get("odds")
        if v not in (None, "", "EVEN"):
            return int(str(v).replace("+", ""))
        if v == "EVEN":
            return 100
    except Exception:
        pass
    legacy = {"home": "homeTeamOdds", "away": "awayTeamOdds"}.get(side)
    if legacy:
        return (o.get(legacy) or {}).get("moneyLine")
    return None


def _parse_comp(comp, ev):
    cs = comp.get("competitors", [])
    if len(cs) != 2:
        return None
    a, b = _competitor(cs[0]), _competitor(cs[1])
    odds = None
    olist = [o for o in (comp.get("odds") or []) if isinstance(o, dict)]
    if olist:
        o = olist[0]
        odds = {
            "details": o.get("details"),
            "home_ml": _ml(o, "home"),
            "away_ml": _ml(o, "away"),
            "draw_ml": _ml(o, "draw"),
        }
    ven = comp.get("venue") or ev.get("venue") or {}
    status = (comp.get("status") or ev.get("status") or {}).get("type", {})
    return {
        "id": str(comp.get("id") or ev.get("id")),
        "name": ev.get("name") if not comp.get("competitors")[0].get("athlete")
                else f'{a["name"]} vs {b["name"]}',
        "date": comp.get("date") or ev.get("date", ""),
        "completed": bool(status.get("completed")),
        "state": status.get("state"),
        "a": a, "b": b,
        "odds": odds,
        "indoor": ven.get("indoor", None),
        "city": (ven.get("address") or {}).get("city"),
    }


def parse_events(js):
    out = []
    for ev in js.get("events", []):
        comps = ev.get("competitions")
        if not comps and ev.get("groupings"):
            comps = [c for g in ev["groupings"] for c in g.get("competitions", [])]
        for comp in comps or []:
            item = _parse_comp(comp, ev)
            if item:
                out.append(item)
    return out


def team_out_count(sport, league, team_id):
    """Number of players listed OUT for a team (NFL/MLB support this well)."""
    try:
        url = f"{BASE}/{sport}/{league}/teams/{team_id}"
        js = S.get(url, params={"enable": "injuries"}, timeout=20).json()
        inj = (js.get("team") or {}).get("injuries") or []
        n = 0
        for i in inj:
            st = i.get("status") or ""
            if isinstance(st, dict):
                st = st.get("type", {}).get("description", "")
            if "out" in str(st).lower():
                n += 1
        return n
    except Exception:
        return 0
