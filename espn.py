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


def _pitcher_name(p0):
    """Player name from an ESPN probables row.

    Live MLB scoreboard rows use displayName for the ROLE ('Probable Starting
    Pitcher') and put the person on athlete.displayName. Older fixtures and a
    few sports put the person on displayName / fullName directly.
    """
    if not isinstance(p0, dict):
        return ""
    athlete = p0.get("athlete") if isinstance(p0.get("athlete"), dict) else {}
    roles = {
        "probable starting pitcher", "starting pitcher", "starter",
        "probable", "pitcher", "sp",
    }
    for cand in (
        athlete.get("displayName"), athlete.get("fullName"), athlete.get("shortName"),
        p0.get("fullName"), p0.get("shortName"), p0.get("athleteDisplayName"),
        p0.get("displayName"),
    ):
        s = str(cand or "").strip()
        if s and s.lower() not in roles:
            return s
    return ""


def _board(sport, league):
    key = (sport, league)
    if key in _board_cache:
        return _board_cache[key]
    out = []
    try:
        today = dt.date.today()
        start = today - dt.timedelta(days=1)
        end = today + dt.timedelta(days=7)
        params = {
            "dates": start.strftime("%Y%m%d") + "-" + end.strftime("%Y%m%d"),
            "limit": 500,
        }
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
                probs = c.get("probables") or []
                p0 = probs[0] if probs and isinstance(probs[0], dict) else {}
                teams.append({"id": str(t.get("id", "")),
                              "name": t.get("displayName", ""),
                              "home": c.get("homeAway") == "home",
                              "pitcher": _pitcher_name(p0)})
            ven = comp.get("venue") or {}
            out.append({"event": str(ev.get("id", "")),
                        "teams": teams,
                        "indoor": ven.get("indoor"),
                        "city": (ven.get("address") or {}).get("city"),
                        "venue": ven.get("fullName"),
                        "kickoff": ev.get("date") or ""})
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


def scores_range(sport, league, start, end, groups=None):
    """Completed games between two dates: [{d, away, home, sa, sb, neutral}].

    Kalshi's settled history says who won but never by how much, which is why Elo
    has been trained with margin-of-victory off. ESPN has the scores, so a history
    built from here can use MOV - and reaches back seasons instead of weeks.
    """
    span = start.strftime("%Y%m%d")
    if end and end != start:
        span += "-" + end.strftime("%Y%m%d")
    params = {"dates": span, "limit": 900}
    if groups:
        params["groups"] = groups
    out = []
    js = S.get(f"{BASE}/{sport}/{league}/scoreboard", params=params, timeout=30).json()
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue                     # in progress, postponed or cancelled
        sides = {}
        for c in comp.get("competitors", []):
            try:
                sides[c.get("homeAway")] = ((c.get("team") or {}).get("displayName", ""),
                                            float(c.get("score")))
            except (TypeError, ValueError):
                sides = {}
                break                    # a game with no score teaches nothing
        if set(sides) != {"home", "away"}:
            continue
        out.append({"d": (ev.get("date") or "")[:10],
                    "away": sides["away"][0], "home": sides["home"][0],
                    "sa": sides["away"][1], "sb": sides["home"][1],
                    "neutral": bool(comp.get("neutralSite"))})
    return out


# How much a player being unavailable is worth, in Elo points. These are priors,
# not fitted values - nobody has enough graded games to fit them - but the ordering
# is not controversial: a starting quarterback is worth more than the rest of the
# roster combined, and a punter is worth almost nothing. The old code counted heads,
# so a third-string long snapper and a franchise QB were both worth 1.
POSITION_ELO = {
    "football": {"QB": 55, "RB": 9, "WR": 8, "TE": 5, "FB": 2,
                 "OT": 7, "OG": 6, "G": 6, "C": 6, "OL": 6, "T": 7,
                 "DE": 8, "DT": 7, "EDGE": 9, "NT": 5, "DL": 7,
                 "LB": 6, "ILB": 6, "OLB": 6, "MLB": 6,
                 "CB": 9, "S": 6, "FS": 6, "SS": 6, "DB": 6,
                 "K": 2, "P": 1, "LS": 1, "PK": 2},
    "baseball": {"SP": 26, "P": 12, "RP": 5, "CP": 7, "C": 7,
                 "1B": 5, "2B": 5, "3B": 5, "SS": 6, "LF": 5, "CF": 6, "RF": 5,
                 "OF": 5, "IF": 4, "DH": 4},
}
DEFAULT_ELO = {"football": 5, "baseball": 5}
# A player who is merely doubtful still plays sometimes; weight the status rather
# than treating every listing as a certainty.
STATUS_WEIGHT = (("out", 1.0), ("injured reserve", 1.0), ("-il", 1.0), (" il", 1.0),
                 ("suspended", 1.0), ("doubtful", 0.7), ("questionable", 0.3))
# One team is never 200 Elo worse because a dozen reserves are listed.
MAX_INJURY_ELO = 90


def _status_weight(status):
    s = " " + str(status or "").lower()
    for word, w in STATUS_WEIGHT:
        if word in s:
            return w
    return 0.0


def _records(block):
    """The injury records inside one team's block of a summary payload."""
    for rec in (block.get("injuries") or []):
        ath = rec.get("athlete") or {}
        pos = (ath.get("position") or {})
        yield {"name": ath.get("displayName") or ath.get("fullName") or "",
               "pos": (pos.get("abbreviation") or pos.get("name") or "").upper(),
               "status": rec.get("status") or "",
               "note": rec.get("shortComment") or ""}


def game_injuries(sport, league, event_id):
    """{team_id: {"elo": points, "out": [record, ...]}} for one game.

    One call covers both teams. The old path asked teams/{id}?enable=injuries,
    which returns no injuries key at all - it had been silently returning 0 for
    every pick ever logged.
    """
    weights = POSITION_ELO.get(sport, {})
    default = DEFAULT_ELO.get(sport, 4)
    out = {}
    try:
        js = S.get(f"{BASE}/{sport}/{league}/summary",
                   params={"event": event_id}, timeout=25).json()
    except Exception as e:
        print(f"[espn] injuries for {event_id}: {type(e).__name__}: {str(e)[:60]}")
        return out
    for block in (js.get("injuries") or []):
        tid = str(((block.get("team") or {}).get("id")) or "")
        if not tid:
            continue
        pts, hurt = 0.0, []
        for rec in _records(block):
            w = _status_weight(rec["status"])
            if not w:
                continue
            val = weights.get(rec["pos"], default) * w
            pts += val
            rec["elo"] = round(val, 1)
            hurt.append(rec)
        hurt.sort(key=lambda r: -r["elo"])
        out[tid] = {"elo": round(min(pts, MAX_INJURY_ELO), 1), "out": hurt}
    return out


def injury_note(side, info, top=2):
    """A short, readable line for the card: who is out and what it costs."""
    if not info or not info.get("out"):
        return ""
    who = ", ".join(f"{r['name']} ({r['pos']})" for r in info["out"][:top] if r["name"])
    more = len(info["out"]) - top
    return (f"{side} -{info['elo']:.0f} Elo: {who}" + (f" +{more} more" if more > 0 else "")) if who else ""


def probable_pitchers(game):
    """{home: name, away: name} from a scoreboard game, omitting blanks."""
    if not game:
        return {}
    out = {}
    for t in game.get("teams") or []:
        if t.get("pitcher"):
            out["home" if t.get("home") else "away"] = t["pitcher"]
    return out


def pitcher_note(game, home_name, away_name):
    """'SP X vs Y' for the card, or '' when ESPN has not listed them."""
    p = probable_pitchers(game)
    if not p:
        return ""
    a = p.get("away") or "?"
    h = p.get("home") or "?"
    return f"SP {away_name}: {a} / {home_name}: {h}"
