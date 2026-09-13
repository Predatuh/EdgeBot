"""Offline checks for the football parlay builder.

Run: python test_parlay.py   (no pytest needed, no network touched)

The point of these is the math, not the plumbing. A parlay builder that is
subtly wrong about probability or payout is worse than no builder at all,
because it looks authoritative either way.
"""
import json
import math
import sys

import parlay

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


def leg(p, ask, event="E", tick=None, league="nfl", flags=None, spread=0.02):
    return {"league": league, "league_label": league.upper(), "event_id": event,
            "ticker": tick or f"{event}-{p}-{ask}", "game": "A vs B", "pick": "A",
            "opp": "B", "home": True, "p": p, "ask": ask, "bid": round(ask - spread, 4),
            "spread": spread, "drag": p / ask, "vol": 500, "oi": 100, "close": "",
            "date": "", "tier": parlay.tier_of(p)[0], "tier_label": "", "emoji": "",
            "flags": flags or [], "notes": ""}


def fake_event(eid, a_name, b_name, a_mid, b_mid, a_ask, b_ask, tie=False, date="2026-09-14"):
    sides = [
        {"name": a_name, "ticker": f"{eid}-{a_name}", "code": a_name, "reg": False,
         "prob": a_mid, "ask": a_ask, "bid": a_ask - 0.02, "vol": 500, "oi": 100,
         "is_tie": False, "home": False},
        {"name": b_name, "ticker": f"{eid}-{b_name}", "code": b_name, "reg": False,
         "prob": b_mid, "ask": b_ask, "bid": b_ask - 0.02, "vol": 500, "oi": 100,
         "is_tie": False, "home": True},
    ]
    if tie:
        sides.append({"name": "Tie", "ticker": f"{eid}-TIE", "code": "TIE", "reg": False,
                      "prob": 0.25, "ask": 0.27, "bid": 0.23, "vol": 10, "oi": 5,
                      "is_tie": True, "home": False})
    return {"event": eid, "series": "KXNFLGAME", "date": date, "close": "", "sides": sides}


# ---------------------------------------------------------------- primitives
print("\nprimitives")
a, b = parlay._devig(0.60, 0.45)
check("de-vig normalises to 1", near(a + b, 1.0) and a > b, f"{a},{b}")
check("de-vig handles a dead market", parlay._devig(0, 0) == (None, None))
check("tier boundary at 97c is a lock", parlay.tier_of(0.97)[0] == "lock")
check("tier just under 97c is not", parlay.tier_of(0.9699)[0] == "strong")
check("a coin flip is a coin flip", parlay.tier_of(0.50)[0] == "live")

# ---------------------------------------------------------------- leg extraction
print("\nleg extraction")
evs = [fake_event("E1", "KC", "TEN", 0.90, 0.12, 0.92, 0.14)]
legs = parlay.legs_from_events(evs, "nfl", "NFL")
check("two-way game yields both sides", len(legs) == 2)
check("de-vigged pair sums to 1", near(sum(l["p"] for l in legs), 1.0, 1e-6),
      str([l["p"] for l in legs]))
check("favourite's p is below its raw mid (vig removed)",
      legs[0]["p"] < 0.90, f"{legs[0]['p']}")
check("ask is preserved as the price you pay", legs[0]["ask"] == 0.92)
check("spread is recorded", near(legs[0]["spread"], 0.02, 1e-9))

no_ask = parlay.legs_from_events([fake_event("E3", "X", "Y", 0.80, 0.20, 0, 0.22)], "nfl", "NFL")
check("a side you cannot buy is dropped", all(l["pick"] != "X" for l in no_ask))
check("...but its opponent is still a usable leg",
      len(no_ask) == 1 and no_ask[0]["pick"] == "Y", str([l["pick"] for l in no_ask]))
check("...priced off the full book, not just the tradeable half",
      near(no_ask[0]["p"], 0.20, 1e-6), f"{no_ask[0]['p']}")

# ---- three-way markets: a draw loses the contract, so it must stay in the book
print("\nthree-way markets (soccer, Test cricket)")
soccer = fake_event("S1", "Arsenal", "Chelsea", 0.45, 0.30, 0.47, 0.32, tie=True)
sl = parlay.legs_from_events([soccer], "epl", "Premier League")
check("only the two teams become legs, never the draw", len(sl) == 2)
check("both legs are marked as losing on a draw", all(l["draw"] for l in sl))
ars = [l for l in sl if l["pick"] == "Arsenal"][0]
check("the draw stays in the denominator",
      near(ars["p"], 0.45 / (0.45 + 0.30 + 0.25), 1e-6), f"{ars['p']}")
check("...which is well below P(win | no draw)",
      ars["p"] < 0.45 / (0.45 + 0.30) - 0.09, f'{ars["p"]} vs {0.45/0.75}')
check("a three-way book still sums to 1 across all three outcomes",
      near(sum(l["p"] for l in sl) + 0.25 / (0.45 + 0.30 + 0.25), 1.0, 1e-6))
two_way = parlay.legs_from_events([fake_event("S2", "KC", "TEN", 0.90, 0.12, 0.92, 0.14)],
                                  "nfl", "NFL")
check("two-way games are unaffected and not marked as draw games",
      not any(l["draw"] for l in two_way) and near(sum(l["p"] for l in two_way), 1.0, 1e-6))

# ---------------------------------------------------------------- the builder
print("\nbuilder")
board = [leg(0.98, 0.985, "g1"), leg(0.96, 0.968, "g2"), leg(0.94, 0.949, "g3"),
         leg(0.91, 0.922, "g4"), leg(0.88, 0.895, "g5"), leg(0.55, 0.565, "g6")]

r = parlay.build(board, target=0.80, min_leg=0.90, max_legs=10)
check("every leg clears the floor", all(l["p"] >= 0.90 for l in r["legs"]))
check("the ticket clears the target", r["win_prob"] >= 0.80, f"{r['win_prob']}")
check("win_prob is exactly the product of the legs",
      near(r["win_prob"], math.prod(l["p"] for l in r["legs"]), 1e-6))
check("multiple is exactly 1 / product of asks",
      near(r["multiple"], round(1 / math.prod(l["ask"] for l in r["legs"]), 2), 1e-9))
check("breakeven is exactly the product of the asks",
      near(r["breakeven"], math.prod(l["ask"] for l in r["legs"]), 1e-6))
check("breakeven is the win rate the multiple demands",
      near(1 / r["breakeven"], r["multiple"], 0.005), f"1/{r['breakeven']} vs {r['multiple']}")
check("no coin flip sneaks in", all(l["p"] > 0.6 for l in r["legs"]))

capped = parlay.build(board, target=0.01, min_leg=0.50, max_legs=3)
check("leg cap is honoured", capped["n"] == 3, f"{capped['n']}")

same_game = [leg(0.95, 0.96, "g1", "A"), leg(0.05, 0.06, "g1", "B"),
             leg(0.93, 0.94, "g2", "C"), leg(0.97, 0.975, "g3", "D")]
one = parlay.build(same_game, target=0.5, min_leg=0.90, max_legs=9, one_per_game=True)
check("one leg per game by default", len({l["event_id"] for l in one["legs"]}) == one["n"])

flagged = [leg(0.98, 0.985, "g1", "A", flags=["QB ruled out"]), leg(0.97, 0.975, "g2", "B")]
clean = parlay.build(flagged, target=0.5, min_leg=0.90, max_legs=9)
check("injury-flagged legs are excluded by default",
      all(not l["flags"] for l in clean["legs"]))
withflag = parlay.build(flagged, target=0.5, min_leg=0.90, max_legs=9, exclude_flagged=False)
check("...and included when you ask for them", withflag["n"] == 2)

check("an impossible target returns nothing",
      parlay.build(board, target=0.999, min_leg=0.99, max_legs=9) is None)
check("an empty board returns nothing", parlay.build([], target=0.5) is None)

# the greedy's whole job: same probability, cheaper spread wins
tie_p = [leg(0.95, 0.99, "g1", "EXPENSIVE"), leg(0.95, 0.955, "g2", "CHEAP")]
pick1 = parlay.build(tie_p, target=0.94, min_leg=0.90, max_legs=1)
check("between equal legs it takes the tighter spread",
      pick1["legs"][0]["ticker"] == "CHEAP", pick1["legs"][0]["ticker"])

# ---------------------------------------------------------------- parlay identities
print("\nparlay identities")
s = parlay.summarise([leg(0.90, 0.92, "g1"), leg(0.80, 0.83, "g2")])
check("EV = win x multiple - 1",
      near(s["ev"], s["win_prob"] * s["multiple"] - 1, 0.005),
      f"{s['ev']} vs {s['win_prob'] * s['multiple'] - 1}")
check("EV = win / breakeven - 1 exactly",
      near(s["ev"], round(s["win_prob"] / s["breakeven"] - 1, 4), 1e-4))
check("EV is negative when you pay above true odds", s["ev"] < 0, f"{s['ev']}")
check("one-in-N matches the win probability",
      s["one_in"] == round(1 / s["win_prob"]), f"{s['one_in']}")
check("a $10 stake pays stake x multiple",
      near(s["payout_per_10"], round(10 * s["multiple"], 2), 1e-9))
check("weakest leg is reported honestly", near(s["weakest_leg"], 0.80))

# THE claim in the module docstring: at a fixed spread in cents - which is how
# Kalshi actually quotes - reaching the same win probability with more legs pays
# strictly less, because each leg takes another bite.
SPREAD = 0.01
def ticket_of(n, win=0.90):
    p = win ** (1.0 / n)
    return parlay.summarise([leg(p, min(p + SPREAD, 0.998), f"x{i}") for i in range(n)])
two, four, twelve = ticket_of(2), ticket_of(4), ticket_of(12)
check("every route reaches the same win probability",
      near(two["win_prob"], 0.90, 1e-6) and near(twelve["win_prob"], 0.90, 1e-6),
      f"{two['win_prob']} {twelve['win_prob']}")
check("more legs pays strictly less for it",
      two["multiple"] > four["multiple"] > twelve["multiple"],
      f"{two['multiple']} {four['multiple']} {twelve['multiple']}")
check("more legs is strictly worse EV",
      two["ev"] > four["ev"] > twelve["ev"],
      f"{two['ev']} {four['ev']} {twelve['ev']}")
# grounding: a real 39-leg slip, $14.99 -> $165.74. A fairly priced parlay paying
# 11.06x has to win 1/11.06 of the time no matter how the legs are sliced.
real = parlay.summarise([leg(0.9402, 0.9402, f"r{i}") for i in range(39)])
check("a fairly priced 39-leg ticket wins 1 / its payout",
      near(real["win_prob"], 1 / real["multiple"], 0.002),
      f"{real['win_prob']:.4f} vs 1/{real['multiple']}")
check("...and that 39-leg slip was a ~9% bet, not a lock",
      0.08 < real["win_prob"] < 0.11, f"{real['win_prob']:.4f}")
check("...with no vig it is exactly break-even", near(real["ev"], 0.0, 1e-3), f"{real['ev']}")

# legs with an identical p/ask ratio are EV-neutral to split - the reason the
# builder ranks on that ratio rather than simply preferring short tickets
flat_2 = parlay.summarise([leg(0.90, 0.92, "a"), leg(0.90, 0.92, "b")])
rp, ra = 0.90 ** 0.5, 0.92 ** 0.5
flat_4 = parlay.summarise([leg(rp, ra, f"x{i}") for i in range(4)])
check("equal-ratio legs split without cost",
      abs(flat_2["ev"] - flat_4["ev"]) < 1e-3, f"{flat_2['ev']} vs {flat_4['ev']}")

# one coin flip halves a safe ticket - the thing that actually cost the user
safe = parlay.summarise([leg(0.97, 0.975, f"s{i}") for i in range(12)])
plus = parlay.summarise([leg(0.97, 0.975, f"s{i}") for i in range(12)] + [leg(0.50, 0.52, "cf")])
check("adding one 50/50 halves the ticket",
      near(plus["win_prob"] / safe["win_prob"], 0.50, 1e-6),
      f"{safe['win_prob']:.4f} -> {plus['win_prob']:.4f}")

# ---------------------------------------------------------------- payout builder
# The rebuild. A price floor made high payouts unreachable and handed back a 1.3x
# ticket labelled "Lottery"; these lock in the objective that replaced it.
print("\npayout builder")
big = []
for i in range(60):
    p = 0.995 - i * 0.0035
    big.append(leg(round(p, 4), round(min(p + 0.008, 0.998), 4), f"gm{i}", f"T{i}"))
mixed_board = big + [leg(0.55, 0.565, f"cf{i}", f"C{i}") for i in range(12)]

r = parlay.build_payout(mixed_board, payout=5.0)
check("a payout target is actually reached", r and r["multiple"] >= 5.0,
      f'{r["multiple"] if r else None}')
check("win probability is close to 1/payout, as it must be",
      abs(r["win_prob"] - 1 / r["multiple"]) < 0.05, f'{r["win_prob"]:.3f} vs {1/r["multiple"]:.3f}')
check("EV is never positive", r["ev"] <= 0, f'{r["ev"]}')

# the finding that motivated the rebuild
chalk = parlay.build_payout(mixed_board, payout=5.0, min_leg=0.88)
free  = parlay.build_payout(mixed_board, payout=5.0, trust_cheap=True)
check("chalk-only needs more legs for the same payout", chalk["n"] > free["n"],
      f'{chalk["n"]} vs {free["n"]}')
check("...and pays worse EV for it", free["ev"] > chalk["ev"], f'{free["ev"]} vs {chalk["ev"]}')

# a payout a board cannot reach must say so rather than return a small ticket
thin = [leg(0.97, 0.975, "g1", "A"), leg(0.96, 0.965, "g2", "B")]
short = parlay.build_payout(thin, payout=250.0)
check("a payout the board cannot reach is flagged, not quietly relabelled",
      short and not short["reached"] and short["multiple"] < 2,
      f'{short["multiple"] if short else None}')
check("...and a reachable one is flagged reached",
      all(parlay.build_payout(mixed_board, p)["reached"] for p in (2.0, 8.0, 40.0)))

# overshoot: asked for 1.04x, a 50c leg pays 1.98x and wins half as often
over = parlay.build_payout([leg(0.95, 0.955, "a", "CHALK"), leg(0.50, 0.505, "b", "FLIP")],
                           payout=1.04, trust_cheap=True)
check("a small target does not grab a leg that blows past it",
      over["legs"][0]["ticker"] == "CHALK", over["legs"][0]["ticker"])
check("...even with the cheap-leg penalty switched off", over["win_prob"] > 0.9)

check("more payout wanted means fewer wins",
      parlay.build_payout(mixed_board, 2.0)["win_prob"]
      > parlay.build_payout(mixed_board, 50.0)["win_prob"])
check("leg cap is honoured", parlay.build_payout(mixed_board, 1e6, max_legs=5)["n"] <= 5)
check("an empty board builds nothing", parlay.build_payout([], 5.0) is None)

# the cheap-leg penalty
cheapish = [leg(0.95, 0.955, "a", "CHALK"), leg(0.50, 0.505, "b", "FLIP")]
pen = parlay.build_payout(cheapish, payout=1.04)
trust = parlay.build_payout(cheapish, payout=1.04, trust_cheap=True)
check("with the penalty on, the favourite is preferred",
      pen["legs"][0]["ticker"] == "CHALK", pen["legs"][0]["ticker"])
check("discount only bites below 90c",
      parlay.discount(0.95) == 1.0 and parlay.discount(0.5) < 1.0)
check("the shown win probability is never discounted",
      abs(pen["win_prob"] - 0.95) < 1e-9, f'{pen["win_prob"]}')

# liquidity replaced the two price sliders
check("a wide book is not liquid", not parlay.is_liquid(leg(0.9, 0.92, "x", spread=0.09)))
check("a thin book is not liquid",
      not parlay.is_liquid(dict(leg(0.9, 0.91, "x", spread=0.01), vol=3, oi=0)))
check("open interest stands in for volume when replaying history",
      parlay.is_liquid(dict(leg(0.9, 0.91, "x", spread=0.01), vol=0, oi=9000)))
check("a tight, traded book is liquid at ANY price",
      parlay.is_liquid(dict(leg(0.42, 0.43, "x", spread=0.01), vol=5000)))
illiquid = [dict(l, vol=0, oi=0) for l in mixed_board]
check("liquid_only can empty a board", parlay.build_payout(illiquid, 5.0) is None)
check("...and turning it off brings the board back",
      parlay.build_payout(illiquid, 5.0, liquid_only=False) is not None)

# ---------------------------------------------------------------- the ladder
print("\nladder")
lad = parlay.ladder(mixed_board)
check("the ladder produces tickets", len(lad) >= 3, str(len(lad)))
check("every ticket carries its preset identity", all("label" in t for t in lad))
wins = [t["win_prob"] for t in lad]
mults = [t["multiple"] for t in lad]
check("riskier tickets win less often", wins == sorted(wins, reverse=True), str(wins))
check("riskier tickets pay more", mults == sorted(mults), str(mults))
check("a Lottery rung actually pays like one",
      [t for t in lad if t["key"] == "lottery"][0]["multiple"] > 50,
      str([t["multiple"] for t in lad]))
sigs = [tuple(sorted(l["ticker"] for l in t["legs"])) for t in lad]
check("no two tickets are the same bet twice", len(sigs) == len(set(sigs)))

thin1 = parlay.ladder([leg(0.99, 0.992, "g1", "ONLY")])
check("a one-game board still returns something sane", len(thin1) >= 1 and thin1[0]["n"] == 1)

# ---------------------------------------------------------------- research targeting
print("\nresearch targeting")
cands = parlay.candidates(mixed_board, cap=30)
check("candidates come back", len(cands) > 0, str(len(cands)))
check("candidates are capped", len(cands) <= 30)
check("no duplicates", len({c["ticker"] for c in cands}) == len(cands))
used = set()
for t in parlay.ladder(mixed_board, exclude_flagged=False):
    for l in t["legs"]:
        used.add(l["ticker"])
check("every leg a ticket would use is a candidate",
      used <= {c["ticker"] for c in cands} or len(used) > 30,
      f'{len(used - {c["ticker"] for c in cands})} missed')
check("cheap legs are researched too, not just chalk",
      any(c["p"] < 0.7 for c in cands) or not any(l["p"] < 0.7 for l in mixed_board))

# ---------------------------------------------------------------- board + render
print("\nboard + render")
bs = parlay.board_summary(big)
check("board counts legs", bs["legs"] == 60)
check("board counts games", bs["games"] == 60)
check("board buckets by tier", sum(bs["by_tier"].values()) == 60)

snap = {"generated_utc": "2026-09-13T12:00Z", "days": 8, "board": bs, "legs": big[:5],
        "presets": parlay.PRESETS, "tickets": lad[:2],
        "calibration_note": parlay.CALIBRATION_NOTE}
html = parlay.render_html(snap)
check("placeholder is gone", "__PARLAY_DATA__" not in html)
check("data is embedded", '"generated_utc"' in html)
hostile = dict(snap)
hostile["legs"] = [dict(big[0], pick="Rice</script><script>alert(1)</script>")]
hostile_html = parlay.render_html(hostile)
start = hostile_html.index('type="application/json">') + len('type="application/json">')
block = hostile_html[start:hostile_html.index("</script>", start)]
check("a team name cannot break out of the data block", "</script>" not in block)
check("...and still round-trips intact",
      json.loads(block)["legs"][0]["pick"] == "Rice</script><script>alert(1)</script>")
check("page carries both theme blocks",
      'prefers-color-scheme: dark' in html and '[data-theme="dark"]' in html)
check("page has a title", "<title>" in html)

lines = parlay.discord_lines(snap, "https://example.test/parlay")
check("discord card leads with the board", "Football parlay board" in lines[0])
check("discord card lists every ticket",
      sum(1 for l in lines if "wins **" in l) == len(snap["tickets"]))
check("discord card links the app", any("example.test" in l for l in lines))
empty_card = parlay.discord_lines({"board": parlay.board_summary([]), "days": 8, "tickets": []})
check("empty board says so rather than inventing a ticket",
      any("too thin" in l for l in empty_card))

# ---------------------------------------------------------------- scopes
print("\nscopes")
mixed = ([leg(0.97, 0.975, f"n{i}", f"NFL{i}", league="nfl") for i in range(4)] +
         [leg(0.98, 0.985, f"c{i}", f"CFB{i}", league="ncaaf") for i in range(6)] +
         [dict(leg(0.55, 0.57, f"s{i}", f"EPL{i}", league="epl"), draw=True) for i in range(4)] +
         [leg(0.80, 0.82, f"t{i}", f"ATP{i}", league="atp") for i in range(3)])
check("nfl scope takes only NFL",
      {l["league"] for l in parlay.scope_legs(mixed, "nfl")} == {"nfl"})
check("college scope takes only NCAAF",
      {l["league"] for l in parlay.scope_legs(mixed, "ncaaf")} == {"ncaaf"})
check("football scope mixes the football leagues",
      {l["league"] for l in parlay.scope_legs(mixed, "football")} == {"nfl", "ncaaf"})
check("everything takes everything", len(parlay.scope_legs(mixed, "all")) == len(mixed))
check("an unknown scope falls through to everything, not to empty",
      len(parlay.scope_legs(mixed, "quidditch")) == len(mixed))

live = parlay.live_scopes(mixed)
keys = [sc["key"] for sc in live]
check("only scopes with legs are offered", "mlb" not in keys and "cricket" not in keys, str(keys))
check("the ones with legs all show up",
      {"nfl", "ncaaf", "football", "soccer", "tennis", "all"} <= set(keys), str(keys))
check("each scope reports its own counts",
      next(sc for sc in live if sc["key"] == "nfl")["legs"] == 4)
check("everything counts the whole board",
      next(sc for sc in live if sc["key"] == "all")["legs"] == len(mixed))
check("an empty board offers no scopes at all", parlay.live_scopes([]) == [])

# a scope is only a filter - it must not change how a ticket is priced
nfl_only = parlay.build(parlay.scope_legs(mixed, "nfl"), target=0.85, min_leg=0.90, max_legs=9)
check("scoping to NFL builds from NFL alone",
      all(l["league"] == "nfl" for l in nfl_only["legs"]))
check("...and prices identically to passing those legs directly",
      parlay.build([l for l in mixed if l["league"] == "nfl"], target=0.85, min_leg=0.90,
                   max_legs=9)["win_prob"] == nfl_only["win_prob"])

check("every scope points at leagues that exist",
      all(not sc["leagues"] or set(sc["leagues"]) <= set(parlay.all_leagues(parlay.load_config()))
          for sc in parlay.SCOPES))

# ---------------------------------------------------------------- injury attribution
# A red flag drops the leg from every ticket, so it has to be right more than it
# has to be sensitive. Every headline below is one the first two live runs really
# produced, and four of the six were flagging the wrong thing.
print("\ninjury attribution")
import datetime as _dt
import research

_NOW = _dt.datetime.now(_dt.timezone.utc)


def brief_for(pick, opp, titles):
    research.configure({})
    research._fetch_rss = lambda q, days: [
        {"title": t, "source": "Test Wire", "when": _NOW} for t in titles]
    return research._headlines("2026-09-13", "College Football",
                               f"{pick} vs {opp}", pick, opp, "football") or {}


def flags_on(pick, opp, title):
    return bool(brief_for(pick, opp, [title]).get("red_flags"))


def noted_on(pick, opp, title):
    b = brief_for(pick, opp, [title])
    return bool(b.get("unattributed")) and not b.get("red_flags")


# "pulls out a win" is the opposite of pulling out. This idiom alone produced two
# of the four bad flags, because the pattern was `pulls? out` with nothing after.
IDIOM = "No. 17 Washington struggles on offense but pulls out 24-10 Apple Cup win over Washington State"
check("winning a game is not an injury", not research.STRONG.search(IDIOM), repr(IDIOM[-40:]))
check("...nor is it worth a flag", not flags_on("Washington St.", "Duquesne", IDIOM))
check("withdrawing still is",
      bool(research.STRONG.search("Alcaraz pulls out of the Shanghai Masters")))

# Our team named only as the OPPONENT, after the injury phrase: the player is SMU's.
SMU = "SMU's Ahmaad Moses Expected to Be Out for Week 2 vs. UC Davis"
check("someone else's injury does not flag our leg", not flags_on("UC Davis", "Stetson", SMU))
check("...it is surfaced as unattributable instead", noted_on("UC Davis", "Stetson", SMU))

# Both sides named: genuinely unknowable, so it cannot be acted on.
RECAP = "Washington Huskies vs. Utah State recap: UW pulls out uninspiring win"
check("a headline naming both sides never flags", not flags_on("Utah", "Utah St.", RECAP))

# The true positives have to survive all of that.
REAL = "No. 11 Oklahoma LB Kip Lewis leaves Michigan game and will miss some time with knee injury"
check("a real injury led by our team still flags", flags_on("Oklahoma", "New Mexico", REAL))
check("so does the plainest possible one",
      flags_on("Alabama", "Wisconsin", "Alabama QB ruled out for the season with torn ACL"))

# Known cost of the leading-name rule: a real injury written subject-first is
# demoted to a note. Shown, not acted on - the safe direction to be wrong in.
DEPTH = "How Ryan Estrada's season-ending injury impacts Minnesota's running back depth"
check("an injury written player-first is demoted, not dropped",
      noted_on("Minnesota", "Akron", DEPTH))

check("a headline about neither team is dropped entirely",
      not brief_for("Iowa", "Northern Iowa", ["Completely unrelated wire story about golf"]))

# ---------------------------------------------------------------- config wiring
print("\nconfig wiring")
cfg = parlay.load_config()
nfl = parlay.league_spec(cfg, "nfl")
check("nfl ticker comes from config.yaml", nfl["ticker"] == "KXNFLGAME", str(nfl))
check("nfl keeps config's ticker order", nfl["ticker_order"] == "away_home")
cfl = parlay.league_spec(cfg, "cfl")
check("cfl falls back to the module's own table", cfl and cfl["ticker"] == "KXCFLGAME")
check("an unknown league is skipped, not guessed", parlay.league_spec(cfg, "nope") is None)
check("the parlay league table does not leak into the bot's leagues",
      "cfl" not in (cfg.get("leagues") or {}))

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + ", ".join(FAILED)))
sys.exit(1 if FAILED else 0)
