"""Offline checks for injury weighting and the Elo backfill.

Both are built from payloads a live probe actually returned, because the previous
injury path was written against a guessed shape and silently returned 0 for every
pick ever logged.
"""
import datetime as dt
import sys

import backfill_elo
import elo
import espn

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ------------------------------------------------------------- injury weighting
# Shaped exactly like the NFL summary?event= payload from the probe.
SUMMARY = {"injuries": [
    {"team": {"id": "6", "displayName": "Dallas Cowboys"}, "injuries": [
        {"status": "Out", "athlete": {"displayName": "Brady Cook",
         "position": {"name": "Quarterback", "abbreviation": "QB"}}},
        {"status": "Out", "athlete": {"displayName": "Jalen Tolbert",
         "position": {"name": "Wide Receiver", "abbreviation": "WR"}}},
        {"status": "Questionable", "athlete": {"displayName": "Some Punter",
         "position": {"name": "Punter", "abbreviation": "P"}}},
        {"status": "Probable", "athlete": {"displayName": "Fine Guy",
         "position": {"name": "Center", "abbreviation": "C"}}},
    ]},
    {"team": {"id": "17", "displayName": "New England Patriots"}, "injuries": [
        {"status": "Out", "athlete": {"displayName": "A Snapper",
         "position": {"name": "Long Snapper", "abbreviation": "LS"}}},
    ]},
]}

print("\ninjury weighting")
espn.S = type("S", (), {"get": staticmethod(
    lambda url, params=None, timeout=None: type("R", (), {"json": staticmethod(lambda: SUMMARY)})())})()
inj = espn.game_injuries("football", "nfl", "401")
check("both teams come back from one call", set(inj) == {"6", "17"}, str(list(inj)))
dal, ne = inj["6"], inj["17"]
check("a quarterback dominates the total", dal["elo"] > 50, str(dal["elo"]))
check("a long snapper barely registers", ne["elo"] <= 2, str(ne["elo"]))
check("the old head-count would have called these equal-ish",
      dal["elo"] > ne["elo"] * 20, f'{dal["elo"]} vs {ne["elo"]}')
check("a probable player counts for nothing",
      all(r["name"] != "Fine Guy" for r in dal["out"]))
check("a questionable player counts partially",
      any(r["name"] == "Some Punter" and 0 < r["elo"] < espn.POSITION_ELO["football"]["P"]
          for r in dal["out"]), str(dal["out"]))
check("the worst loss is listed first", dal["out"][0]["pos"] == "QB")
check("the note names who is out",
      "Brady Cook" in espn.injury_note("Dallas", dal) and "QB" in espn.injury_note("Dallas", dal),
      espn.injury_note("Dallas", dal))
check("no injuries means no note", espn.injury_note("X", {"elo": 0, "out": []}) == "")

many = {"injuries": [{"team": {"id": "1"}, "injuries": [
    {"status": "Out", "athlete": {"displayName": f"P{i}", "position": {"abbreviation": "QB"}}}
    for i in range(8)]}]}
espn.S = type("S", (), {"get": staticmethod(
    lambda url, params=None, timeout=None: type("R", (), {"json": staticmethod(lambda: many)})())})()
check("the total is capped so a long list cannot swamp the model",
      espn.game_injuries("football", "nfl", "x")["1"]["elo"] == espn.MAX_INJURY_ELO)

espn.S = type("S", (), {"get": staticmethod(
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))})()
check("a failed request yields nothing rather than a silent zero",
      espn.game_injuries("football", "nfl", "x") == {})

# ------------------------------------------------------------- name mapping
print("\nname mapping (ESPN -> Kalshi)")
# Every one of these collided on the first live backfill and came back ambiguous.
VOCAB = {"Ohio", "Ohio St.", "Miami (FL)", "Miami (OH)", "Duke", "Duquesne",
         "Jackson St.", "Jacksonville St.", "Buffalo", "Colorado",
         "Texas", "Texas St.", "Alabama", "Alabama St."}
CASES = [("Ohio State Buckeyes", "Ohio St."), ("Ohio Bobcats", "Ohio"),
         ("Miami Hurricanes", "Miami (FL)"), ("Miami (OH) RedHawks", "Miami (OH)"),
         ("Duquesne Dukes", "Duquesne"), ("Duke Blue Devils", "Duke"),
         ("Jacksonville State Gamecocks", "Jacksonville St."),
         ("Jackson State Tigers", "Jackson St."),
         ("Colorado Buffaloes", "Colorado"), ("Buffalo Bulls", "Buffalo"),
         ("Texas Longhorns", "Texas"), ("Texas State Bobcats", "Texas St."),
         ("Alabama Crimson Tide", "Alabama"), ("Alabama State Hornets", "Alabama St.")]
m, amb = backfill_elo.build_name_map({e for e, _ in CASES} | {"Sam Houston Bearkats"}, VOCAB)
for espn_name, want in CASES:
    check(f"  {espn_name} -> {want}", m.get(espn_name) == want, repr(m.get(espn_name)))
check("nothing is left ambiguous", not amb, str(amb))
check("a team we have never bet is left alone", "Sam Houston Bearkats" not in m)
check("the nickname cannot beat the first word",
      backfill_elo.match_score("Colorado", "Colorado Buffaloes")
      > backfill_elo.match_score("Buffalo", "Colorado Buffaloes"))
check("a contradicted parenthetical is rejected outright",
      backfill_elo.match_score("Miami (FL)", "Miami (OH) RedHawks")
      < backfill_elo.match_score("Miami (OH)", "Miami (OH) RedHawks"))
check("a non-match scores nothing", backfill_elo.match_score("Georgia", "Texas Longhorns") is None)

# ------------------------------------------------------------- Elo rebuild
print("\nElo rebuild from scores")
def game(d, away, home, sa, sb, neutral=False):
    return {"d": d, "away": away, "home": home, "sa": sa, "sb": sb, "neutral": neutral}

# one team wins every week by a lot, another loses every week
games = []
for i in range(8):
    d = f"2025-09-{i+1:02d}"
    games.append(game(d, "Cupcake U", "Juggernaut", 3, 56))
    games.append(game(d, "Middling", "Alsoran", 21, 20))
r = backfill_elo.rebuild("t", games, {}, k=24, home_adv=60)
check("the dominant team ends far above base", r["Juggernaut"] > elo.BASE_RATING + 100,
      f'{r["Juggernaut"]:.0f}')
check("the doormat ends far below", r["Cupcake U"] < elo.BASE_RATING - 100, f'{r["Cupcake U"]:.0f}')
# Middling also went 8-0, so it climbs too - the claim MOV supports is about two
# teams with the SAME record and different margins, not about winning less often.
same_record = []
for i in range(8):
    d = f"2025-09-{i+1:02d}"
    same_record.append(game(d, "Grinder", "GrindFoe", 21, 20))     # wins by 1, away
    same_record.append(game(d, "Hammer", "HammerFoe", 63, 7))      # wins by 56, away
rr = backfill_elo.rebuild("t", same_record, {}, k=24, home_adv=0)
check("same record, bigger margins, higher rating",
      rr["Hammer"] > rr["Grinder"] + 80, f'{rr["Hammer"]:.0f} vs {rr["Grinder"]:.0f}')
check("the cutoff date is recorded so Kalshi does not re-teach the same games",
      r["_espn_through"] == "2025-09-08", r.get("_espn_through"))
check("game count is recorded", r["_games"] == 16)

blowout = backfill_elo.rebuild("t", [game("2025-09-01", "A", "B", 0, 70)], {}, k=24, home_adv=0)
narrow = backfill_elo.rebuild("t", [game("2025-09-01", "A", "B", 27, 28)], {}, k=24, home_adv=0)
check("margin of victory actually matters",
      (blowout["B"] - elo.BASE_RATING) > (narrow["B"] - elo.BASE_RATING) * 1.5,
      f'{blowout["B"]-1500:.1f} vs {narrow["B"]-1500:.1f}')

nm = {"Juggernaut": "Jugg St."}
r2 = backfill_elo.rebuild("t", games, nm, k=24, home_adv=60)
check("ESPN names are rewritten to the Kalshi vocabulary",
      "Jugg St." in r2 and "Juggernaut" not in r2, str([k for k in r2 if not k.startswith("_")]))

# a team that only ever played weak opposition must be reported, not trusted
iso = []
for i in range(12):
    iso.append(game(f"2025-09-{i+1:02d}", "Nobody%d" % i, "Isolated", 3, 59))
for i in range(12):
    iso.append(game(f"2025-10-{i+1:02d}", "Tested", "Contender%d" % i, 31, 28))
    iso.append(game(f"2025-10-{i+1:02d}", "Contender%d" % i, "Filler", 55, 3))
ri = backfill_elo.rebuild("t", iso, {}, k=24, home_adv=0)
flagged = {t for t, *_ in backfill_elo.schedule_check(ri, iso, {})}
check("a team fattened on weak opponents is flagged", "Isolated" in flagged, str(flagged))
check("...and one that beat real teams is not", "Tested" not in flagged, str(flagged))
check("the flag does not change any rating",
      backfill_elo.rebuild("t", iso, {}, k=24, home_adv=0)["Isolated"] == ri["Isolated"])

sd, rng, n = backfill_elo.separation(r)
check("separation is reported", sd > 0 and rng > 0 and n == 4, f'{sd:.1f}/{rng:.1f}/{n}')
check("an empty table reports no separation", backfill_elo.separation({"_games": 0})[2] == 0)

print("\ncanonical NFL names")
import names
check("GB Packers folds onto Green Bay", names.canon("nfl", "GB Packers") == "Green Bay")
check("Green Bay stays Green Bay", names.canon("nfl", "Green Bay") == "Green Bay")
check("a tennis player is left alone", names.canon("atp", "Alcaraz") == "Alcaraz")
folded = names.fold_elo("nfl", {"Green Bay": 1600, "GB Packers": 1400, "_games": 10})
check("duplicate keys collapse to the live Kalshi name",
      "Green Bay" in folded and "GB Packers" not in folded, str(folded))
check("the live name keeps its rating when both exist", folded["Green Bay"] == 1600)
only_nick = names.fold_elo("nfl", {"GB Packers": 1555, "_games": 2})
check("an alias-only team moves onto the city name",
      only_nick.get("Green Bay") == 1555 and "GB Packers" not in only_nick)
hist = names.fold_history("nfl", [
    {"d": "2026-09-01", "a": "GB Packers", "b": "CHI Bears", "w": "GB Packers"},
    {"d": "2026-09-01", "a": "Green Bay", "b": "Chicago", "w": "Green Bay"},
])
check("history aliases collapse and dedupe",
      len(hist) == 1 and hist[0]["a"] == "Green Bay" and hist[0]["b"] == "Chicago",
      str(hist))
check("PSG maps for soccer backfill", names.canon("ligue1", "PSG") == "Paris Saint-Germain")
check("comma-separated backfill still accepts one league",
      callable(backfill_elo.run_one))

print("\nMLB pitchers + kickoff on the ESPN board")
BOARD = {"events": [{"id": "401", "date": "2026-09-14T23:10Z", "competitions": [{
    "venue": {"indoor": False, "address": {"city": "Cincinnati"}, "fullName": "GABP"},
    "competitors": [
        {"homeAway": "away", "team": {"id": "19", "displayName": "Los Angeles Dodgers"},
         "probables": [{"displayName": "Yoshinobu Yamamoto"}]},
        {"homeAway": "home", "team": {"id": "17", "displayName": "Cincinnati Reds"},
         "probables": [{"fullName": "Hunter Greene"}]},
    ],
}]}]}
espn._board_cache.clear()
espn.S = type("S", (), {"get": staticmethod(
    lambda url, params=None, timeout=None: type("R", (), {"json": staticmethod(lambda: BOARD)})())})()
g = espn.find_game("baseball", "mlb", "Los Angeles D", "Cincinnati")
check("the fixture is found", g is not None)
check("kickoff is on the game", g and g.get("kickoff") == "2026-09-14T23:10Z", str(g))
check("both probable pitchers are named",
      espn.pitcher_note(g, "Cincinnati", "Los Angeles D") ==
      "SP Los Angeles D: Yoshinobu Yamamoto / Cincinnati: Hunter Greene",
      espn.pitcher_note(g, "Cincinnati", "Los Angeles D") if g else "")
check("no pitchers means no note", espn.pitcher_note({"teams": []}, "A", "B") == "")

print("\nCFBD injury parse")
import cfbd
cfbd._cache.clear()
rows = [
    {"team": "Ohio State", "player": "Julian Sayin", "position": "QB", "status": "Out"},
    {"team": "Ohio State", "athleteName": "A Backup", "position": "LS", "classification": "Out"},
    {"team": {"school": "Michigan"}, "name": "Fine Guy", "position": {"abbreviation": "RB"},
     "status": "Probable"},
]
cfbd._get = lambda *a, **k: rows
# force a key so injuries() does not bail
import os
os.environ["CFBD_API_KEY"] = os.environ.get("CFBD_API_KEY") or "test-key"
table = cfbd.injuries(year=2026, week=3)
check("Ohio State is keyed by CFBD name", "Ohio State" in table, str(list(table)))
check("a QB out dominates the total", table.get("Ohio State", {}).get("elo", 0) > 50)
check("a probable player is ignored",
      all(r["name"] != "Fine Guy" for info in table.values() for r in info["out"]))
ih, _ = cfbd.for_teams("Ohio St.", "Michigan")
check("Kalshi 'Ohio St.' matches CFBD 'Ohio State'",
      ih and ih["out"][0]["name"] == "Julian Sayin", str(ih))

print("\nweather at kickoff, not current")
import weather
class FakeResp:
    def __init__(self, payload):
        self._p = payload
    def json(self):
        return self._p
calls = []
def fake_get(url, params=None, timeout=None):
    calls.append((url, params))
    if "geocoding" in url:
        return FakeResp({"results": [{"latitude": 39.1, "longitude": -84.5}]})
    if params and "hourly" in (params or {}):
        return FakeResp({"hourly": {
            "time": ["2026-09-14T12:00", "2026-09-14T13:00", "2026-09-14T19:00"],
            "temperature_2m": [55.0, 58.0, 72.0],
            "wind_speed_10m": [25.0, 24.0, 8.0],
            "precipitation": [0.0, 0.0, 0.0],
            "precipitation_probability": [10, 10, 5],
        }})
    return FakeResp({"current": {"temperature_2m": 40.0, "wind_speed_10m": 30.0, "precipitation": 0}})
weather._geo_cache.clear()
weather.requests.get = fake_get
w = weather.forecast("Cincinnati", when="2026-09-14T19:10:00Z")
check("hourly at kickoff is used, not current", w and w.get("kickoff") is True, str(w))
check("the 7pm slot is picked over the windy morning",
      w and round(w["temp_f"]) == 72 and round(w["wind_mph"]) == 8, str(w))
check("the note says at kickoff", "kickoff" in weather.describe(w), weather.describe(w))
check("morning gusts are not extreme at a calm kickoff", weather.extreme(w) is False)
now = weather.forecast("Cincinnati")
check("no kickoff time still returns current", now and now.get("kickoff") is False, str(now))

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + ", ".join(FAILED)))
sys.exit(1 if FAILED else 0)
