#!/usr/bin/env python3
"""Team efficiency per play, from play-by-play - a rating that does not come from
who won.

Elo only knows results. Two teams at 1500 can be a good team that keeps losing
close games and a bad team that keeps winning them, and Elo cannot tell them
apart until the results eventually diverge. EPA per play can, immediately: it
measures what a team does on every snap against what an average team does in the
same down, distance and field position.

Sources, both free:

  NFL     nflverse's play-by-play, which ships nflfastR's own `epa` column.
          No key, no account - a gzipped CSV per season off a GitHub release.
  NCAAF   collegefootballdata.com's PPA endpoints. PPA is the same idea under a
          different name. Needs a free API key in CFBD_API_KEY; without it this
          league is skipped and says so, rather than guessing.

  (cfbfastR-data used to publish college play-by-play the same way nflverse does.
   Every layout it ever used now 404s, which is why college goes through CFBD.)

Garbage time is excluded on the NFL side: plays with win probability outside
10-90% are a different game being played, and including them makes blowout
winners look better than they are.
"""
import csv
import datetime as dt
import gzip
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "v2")
UA = {"User-Agent": "EdgeBot/2"}

NFL_PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_%d.csv.gz"
CFBD = "https://api.collegefootballdata.com"

WP_LOW, WP_HIGH = 0.10, 0.90      # outside this, it is garbage time
MIN_PLAYS = 120                   # ~2 games. Below this a team's number is noise.
# In week 1 a team has about 60 plays, and a 17-play sample said Jacksonville was
# the best offence in football. Last season is carried at this weight so the
# number means something in September; because the blend is a play-weighted mean,
# the prior fades on its own as the new season accumulates - it is worth about
# 350 plays, so this season takes over around week 6.
PRIOR_WEIGHT = 0.35

# nflverse uses abbreviations; Kalshi names NFL teams by city/region. Mapping the
# 32 by hand because a fuzzy match here would silently rate the wrong team.
NFL_TEAMS = {
    "ARI": "Arizona", "ATL": "Atlanta", "BAL": "Baltimore", "BUF": "Buffalo",
    "CAR": "Carolina", "CHI": "Chicago", "CIN": "Cincinnati", "CLE": "Cleveland",
    "DAL": "Dallas", "DEN": "Denver", "DET": "Detroit", "GB": "Green Bay",
    "HOU": "Houston", "IND": "Indianapolis", "JAX": "Jacksonville", "KC": "Kansas City",
    "LA": "Los Angeles Rams", "LAR": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas", "MIA": "Miami", "MIN": "Minnesota", "NE": "New England",
    "NO": "New Orleans", "NYG": "New York Giants", "NYJ": "New York Jets",
    "PHI": "Philadelphia", "PIT": "Pittsburgh", "SEA": "Seattle", "SF": "San Francisco",
    "TB": "Tampa Bay", "TEN": "Tennessee", "WAS": "Washington",
}

# EPA per play -> a points margin. 65 plays a game is the modern average in both
# codes, and a team's net EPA already carries both sides of the ball, so the
# difference of two nets over a game is the expected margin.
PLAYS_PER_GAME = 65
# Two corrections, both of which matter more than the conversion:
#   SHRINK  season-to-date efficiency overstates how different teams really are.
#           Regressing it toward zero is what makes it predictive rather than
#           descriptive.
#   CAP     no play-level sample gets to call a football game by more than three
#           touchdowns. A thin sample that says otherwise is wrong, not brave.
SHRINK = 0.70
CAP_POINTS = 21.0
# Points of margin per standard deviation of NFL/NCAAF game outcomes. This is what
# turns a projected margin into a probability, and it is the number that keeps a
# 20-point projection at 93% rather than 99%.
MARGIN_SIGMA = 13.5


def _f(x, default=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if v == v else default            # NaN is not a number we want


def path(league):
    return os.path.join(DATA, f"epa_{league}.json")


def load(league):
    try:
        with open(path(league), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(league, table):
    os.makedirs(DATA, exist_ok=True)
    with open(path(league), "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1, sort_keys=True)
    return path(league)


# ------------------------------------------------------------------------ NFL
def _blank():
    return {"off_plays": 0, "off_epa": 0.0, "off_success": 0,
            "pass_plays": 0, "pass_epa": 0.0, "rush_plays": 0, "rush_epa": 0.0,
            "def_plays": 0, "def_epa": 0.0, "def_success": 0}


def nfl_table(season=None, prior_weight=PRIOR_WEIGHT, timeout=180):
    """This season, with last season carried at a decayed weight behind it."""
    season = season or _season_year()
    cur = nfl_season(season, timeout=timeout)
    if prior_weight <= 0:
        return cur
    try:
        prior = nfl_season(season - 1, timeout=timeout)
    except Exception as e:
        print(f"[epa] no {season - 1} to lean on ({type(e).__name__}); "
              f"using {season} alone")
        cur["_prior"] = None
        return cur
    return blend(cur, prior, prior_weight)


def blend(cur, prior, w):
    """A play-weighted mean of two seasons' raw sums.

    Weighting the SUMS rather than the per-play averages is what makes the prior
    fade by itself: it contributes a fixed number of effective plays, so its share
    shrinks as this season's real plays pile up. Averaging the averages would let
    last season keep half the vote in December.
    """
    out = dict(cur)
    out["_prior"] = {"season": prior.get("_season"), "weight": w,
                     "teams": len(prior.get("teams") or {})}
    teams = {}
    names = set(cur.get("_raw") or {}) | set(prior.get("_raw") or {})
    for name in names:
        c = (cur.get("_raw") or {}).get(name) or _blank()
        p = (prior.get("_raw") or {}).get(name) or _blank()
        merged = {k: c[k] + w * p[k] for k in c}
        done = _finish(merged)
        if done is None:
            continue
        done["this_season_plays"] = c["off_plays"]
        teams[name] = done
    out["teams"] = teams
    out.pop("_raw", None)
    return out


def nfl_season(season=None, url=None, timeout=180):
    """Every offensive and defensive play of the season, aggregated per team.

    Streamed and parsed row by row: the file is tens of megabytes by December and
    there is no reason to hold it in memory, or to add pandas to this project for
    a sum and a count.
    """
    season = season or _season_year()
    url = url or (NFL_PBP % season)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode("utf-8", "replace")
    return nfl_from_csv(io.StringIO(text), season)


def nfl_from_csv(handle, season=None):
    teams, rows, used = {}, 0, 0
    for row in csv.DictReader(handle):
        rows += 1
        epa = _f(row.get("epa"))
        off, dfn = row.get("posteam"), row.get("defteam")
        if epa is None or not off or not dfn:
            continue
        if row.get("play_type") not in ("pass", "run"):
            continue
        wp = _f(row.get("wp"))
        if wp is not None and not (WP_LOW <= wp <= WP_HIGH):
            continue                        # garbage time is a different game
        used += 1
        o = teams.setdefault(off, _blank())
        d = teams.setdefault(dfn, _blank())
        succ = 1 if _f(row.get("success"), 0) >= 1 else 0
        o["off_plays"] += 1
        o["off_epa"] += epa
        o["off_success"] += succ
        if row.get("play_type") == "pass":
            o["pass_plays"] += 1
            o["pass_epa"] += epa
        else:
            o["rush_plays"] += 1
            o["rush_epa"] += epa
        d["def_plays"] += 1
        d["def_epa"] += epa                 # EPA the defence allowed
        d["def_success"] += succ
    out = {"_league": "nfl", "_season": season, "_source": "nflverse play-by-play",
           "_rows": rows, "_plays_used": used,
           "_generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "teams": {}, "_raw": {}, "_unmapped": []}
    for abbr, t in sorted(teams.items()):
        name = NFL_TEAMS.get(abbr)
        if not name:
            # a relocation or a rename would otherwise drop a team in silence
            out["_unmapped"].append(abbr)
            continue
        # raw keeps everything so a blend can still use a team that has only been
        # seen on one side of the ball yet; only complete teams get a rating
        out["_raw"][name] = t
        if t["off_plays"] and t["def_plays"]:
            out["teams"][name] = _finish(t)
    if out["_unmapped"]:
        print(f"[epa] nfl abbreviations with no Kalshi name: {out['_unmapped']}")
    return out


def _finish(t):
    o, d = t["off_plays"] or 0, t["def_plays"] or 0
    if not o or not d:
        return None
    return {
        "off_epa": round(t["off_epa"] / o, 4),
        "def_epa": round(t["def_epa"] / d, 4),          # lower is better
        "net_epa": round(t["off_epa"] / o - t["def_epa"] / d, 4),
        "off_success": round(t["off_success"] / o, 4),
        "def_success": round(t["def_success"] / d, 4),
        "pass_epa": round(t["pass_epa"] / t["pass_plays"], 4) if t["pass_plays"] else None,
        "rush_epa": round(t["rush_epa"] / t["rush_plays"], 4) if t["rush_plays"] else None,
        "off_plays": o, "def_plays": d,
    }


def _season_year():
    """Football seasons are named for the year they start in."""
    now = dt.date.today()
    return now.year if now.month >= 7 else now.year - 1


# ---------------------------------------------------------------------- NCAAF
def _cfbd(endpoint, params, key, timeout=60):
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(f"{CFBD}{endpoint}?{q}",
                                 headers=dict(UA, Authorization=f"Bearer {key}"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ncaaf_season(season=None, key=None, timeout=60):
    """Per-team PPA from collegefootballdata.com.

    PPA is CFBD's name for the same quantity nflfastR calls EPA: how much a play
    changed the expected points. Garbage time is excluded at source.

    Returns None - never a guess - when there is no key. The alternative was
    deriving efficiency from ESPN drive charts, which is a different and weaker
    statistic that would be dishonest to store in a column called epa.
    """
    key = key or os.environ.get("CFBD_API_KEY", "").strip()
    season = season or _season_year()
    if not key:
        print("[epa] no CFBD_API_KEY, so no college EPA. A free key from "
              "collegefootballdata.com turns this on.")
        return None
    rows = _cfbd("/ppa/teams", {"year": season, "excludeGarbageTime": "true"},
                 key, timeout)
    out = {"_league": "ncaaf", "_season": season, "_source": "collegefootballdata PPA",
           "_rows": len(rows),
           "_generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "teams": {}, "_raw": {}}
    for r in rows:
        name = r.get("team")
        off, dfn = r.get("offense") or {}, r.get("defense") or {}
        o, d = _f(off.get("overall")), _f(dfn.get("overall"))
        if not name or o is None or d is None:
            continue
        out["teams"][name] = {
            "off_epa": round(o, 4), "def_epa": round(d, 4),
            "net_epa": round(o - d, 4),
            "pass_epa": _round(off.get("passing")), "rush_epa": _round(off.get("rushing")),
            "off_success": None, "def_success": None,
            # CFBD reports rates, not counts; plays are what gates a rating, so a
            # season-to-date team is treated as having played its games' worth
            "off_plays": None, "def_plays": None,
        }
    return out


def _round(x, n=4):
    v = _f(x)
    return round(v, n) if v is not None else None


def ncaaf_table(season=None, key=None, prior_weight=PRIOR_WEIGHT, timeout=60):
    """This season's PPA, with last season behind it while the sample is thin.

    CFBD gives rates rather than play counts, so the prior cannot fade by itself
    the way the NFL one does. It fades on the calendar instead: full weight in
    week 1, gone by week 8, which is about where a college team's own season
    stops being noise.
    """
    season = season or _season_year()
    cur = ncaaf_season(season, key, timeout)
    if cur is None:
        return None
    week = _weeks_in(season)
    w = prior_weight * max(0.0, 1.0 - week / 8.0)
    cur["_week"] = week
    if w <= 0.01:
        cur["_prior"] = None
        return cur
    try:
        prior = ncaaf_season(season - 1, key, timeout)
    except Exception as e:
        print(f"[epa] no {season - 1} college to lean on ({type(e).__name__})")
        cur["_prior"] = None
        return cur
    if not prior:
        cur["_prior"] = None
        return cur
    cur["_prior"] = {"season": season - 1, "weight": round(w, 3),
                     "teams": len(prior["teams"])}
    for name, t in cur["teams"].items():
        p = prior["teams"].get(name)
        if not p:
            continue
        for f in ("off_epa", "def_epa", "pass_epa", "rush_epa"):
            a, b = t.get(f), p.get(f)
            if a is not None and b is not None:
                t[f] = round((a + w * b) / (1 + w), 4)
        t["net_epa"] = round(t["off_epa"] - t["def_epa"], 4)
    return cur


def _weeks_in(season):
    """Weeks of the season played so far. College starts in late August."""
    start = dt.date(season, 8, 25)
    return max(0, (dt.date.today() - start).days // 7)


# ------------------------------------------------------------------- the edge
def matchup(table, home, away, min_plays=MIN_PLAYS, home_points=0.0):
    """What the play-level data alone says this game should be, as a margin and a
    win probability for the home side.

    This is deliberately NOT an adjustment to Elo. Elo already knows who has been
    winning, and most of what EPA knows is in there too; adding one to the other
    counts the same evidence twice, which is how an early version of this turned a
    good team into a 717-Elo favourite. It is a second opinion, to be blended with
    Elo at a weight, not stacked on top of it.

    Both sides must be rated and, where the source gives play counts, both must
    clear the sample floor - half a comparison is worse than none, because it reads
    as the rated team being better than a team we simply cannot see.
    """
    if not table:
        return None
    teams = table.get("teams") or {}
    h, a = teams.get(home), teams.get(away)
    if not h or not a:
        return None
    for t in (h, a):
        if t.get("off_plays") is not None and t["off_plays"] < min_plays:
            return None
    diff = h["net_epa"] - a["net_epa"]                       # EPA per play
    raw = diff * PLAYS_PER_GAME                              # ...over a game
    points = max(-CAP_POINTS, min(CAP_POINTS, raw * SHRINK + home_points))
    return {"points": round(points, 2), "raw_points": round(raw, 2),
            "epa_diff": round(diff, 4), "p_home": round(_p_from_margin(points), 4),
            "home": h, "away": a}


def _p_from_margin(points, sigma=MARGIN_SIGMA):
    """Normal CDF of the margin. math.erf keeps this to the standard library."""
    import math
    return 0.5 * (1.0 + math.erf(points / (sigma * math.sqrt(2.0))))


def note(m, home, away):
    """One line for the card, in points rather than EPA - nobody bets EPA."""
    if not m:
        return ""
    side = home if m["points"] >= 0 else away
    return (f"epa {side} by {abs(m['points']):.1f} ({100 * m['p_home']:.0f}% home) "
            f"[{home} {m['home']['net_epa']:+.3f} / {away} {m['away']['net_epa']:+.3f} per play]")


# ---------------------------------------------------------------------- CLI
def refresh(league, season=None, key=None, prior_weight=PRIOR_WEIGHT):
    """Pull a league's table and write it. Returns the table, or None."""
    if league == "nfl":
        t = nfl_table(season, prior_weight)
    elif league == "ncaaf":
        t = ncaaf_table(season, key, prior_weight)
    else:
        print(f"[epa] no play-by-play source wired up for {league}")
        return None
    if not t or not t.get("teams"):
        print(f"[epa] {league}: nothing to write")
        return None
    save(league, t)
    rows = sorted(t["teams"].items(), key=lambda kv: -kv[1]["net_epa"])
    print(f"[epa] {league}: {len(rows)} teams from {t['_source']}"
          + (f", {t['_prior']['season']} carried at {t['_prior']['weight']}"
             if t.get("_prior") else "")
          + f" -> {path(league)}")
    for name, x in rows[:5]:
        print(f"[epa]   {name:<22} net {x['net_epa']:+.3f}  off {x['off_epa']:+.3f}  "
              f"def {x['def_epa']:+.3f}")
    print("[epa]   ...")
    for name, x in rows[-3:]:
        print(f"[epa]   {name:<22} net {x['net_epa']:+.3f}  off {x['off_epa']:+.3f}  "
              f"def {x['def_epa']:+.3f}")
    return t


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Team efficiency from play-by-play")
    ap.add_argument("--league", default="nfl,ncaaf",
                    help="comma separated: nfl, ncaaf")
    ap.add_argument("--season", type=int, default=0, help="0 = the current one")
    ap.add_argument("--prior-weight", type=float, default=PRIOR_WEIGHT,
                    help="how much of last season to carry while this one is thin")
    a = ap.parse_args(argv)
    done = 0
    for lg in [x.strip() for x in a.league.split(",") if x.strip()]:
        try:
            if refresh(lg, a.season or None, prior_weight=a.prior_weight):
                done += 1
        except Exception as e:
            print(f"[epa] {lg} failed: {type(e).__name__}: {str(e)[:140]}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
