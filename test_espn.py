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

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + ", ".join(FAILED)))
sys.exit(1 if FAILED else 0)
