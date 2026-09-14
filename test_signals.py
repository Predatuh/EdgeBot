#!/usr/bin/env python3
"""Offline checks on the two new signals: market flow, and EPA.

No network. The tape is fed in as fixtures through kalshi._get, and the
play-by-play as a CSV string, so the parsing is under test too - that is where
the last two silent failures in this project lived.
"""
import io

import epa
import flow
import kalshi

FAIL = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def candle(ts, bid, ask, vol=10):
    """Kalshi's real candle shape: dollar STRINGS, not cents."""
    return {"end_period_ts": ts,
            "yes_bid": {"close_dollars": f"{bid:.4f}"},
            "yes_ask": {"close_dollars": f"{ask:.4f}"},
            "volume": vol, "open_interest": 100}


print("price history")
T0 = 1_789_000_000
kalshi.candles = lambda *a, **k: [candle(T0, .50, .52), candle(T0 + 3600, .55, .57),
                                  candle(T0 + 7200, .60, .62)]
h = flow.history("S", "T", now=T0 + 7200)
check("candles parse as dollars, not cents", len(h) == 3 and h[0]["mid"] == 0.51,
      str(h[0]["mid"] if h else None))
check("history comes back oldest first", h[0]["ts"] < h[-1]["ts"])
m = flow.move(h)
check("open is where it started", m["open"] == 0.51)
check("now is where it is", m["now"] == 0.61)
check("delta is the travel", abs(m["delta"] - 0.10) < 1e-9, str(m["delta"]))
check("hours are measured, not assumed", m["hours"] == 2.0)

kalshi.candles = lambda *a, **k: [candle(T0, .50, .52)]
check("one quote is not a move", flow.move(flow.history("S", "T", now=T0)) is None)
kalshi.candles = lambda *a, **k: [candle(T0, .50, .52), {"end_period_ts": T0 + 60}]
check("a candle with no price is dropped, not defaulted",
      len(flow.history("S", "T", now=T0 + 60)) == 1)
kalshi.candles = lambda *a, **k: []
check("no history is not a crash", flow.history("S", "T") == [])
def boom(*a, **k):
    raise RuntimeError("kalshi is down")
kalshi.candles = boom
check("a broken feed is empty, not an exception", flow.history("S", "T") == [])

print("\nwhich way it moved")
def mv(d):
    return {"move": {"delta": d}}
check("a 5c rise is steam toward the side", flow.verdict(mv(0.05)) == "toward")
check("a 5c fall is a drift away", flow.verdict(mv(-0.05)) == "away")
check("a cent is noise, not a move", flow.verdict(mv(0.01)) == "flat")
check("nothing to read is silence", flow.verdict(None) == "" and flow.note(None, "X") == "")

print("\nthe tape: tickets against money")
def tape(trades):
    kalshi._get = lambda path, params, **k: {"trades": trades}
t = [{"count": 1, "taker_side": "yes"} for _ in range(9)] + [{"count": 900, "taker_side": "no"}]
tape(t)
f = flow.taker_flow("T")
check("prints are counted as tickets", f["trades"] == 10)
check("contracts are counted as money", f["contracts"] == 909)
check("tickets say one side", abs(f["ticket_share"] - 0.9) < 1e-9, str(f["ticket_share"]))
check("money says the other", f["money_share"] < 0.02, str(f["money_share"]))
check("the gap is money minus tickets", abs(f["gap"] - (f["money_share"] - f["ticket_share"])) < 1e-9)
check("the biggest print is kept", f["max_size"] == 900)
check("that shape counts as a disagreement", flow.disagrees({"tape": f}))

tape([{"count": 50, "taker_side": "yes"} for _ in range(10)])
f2 = flow.taker_flow("T")
check("one-sided flow is not a disagreement", not flow.disagrees({"tape": f2}),
      f"tickets {f2['ticket_share']} money {f2['money_share']}")
tape([{"count": 10, "taker_side": "yes"}] * 6 + [{"count": 200, "taker_side": "yes"}])
check("both sides agreeing is not a disagreement either",
      not flow.disagrees({"tape": flow.taker_flow("T")}))
tape([{"count": 5, "taker_side": ""}, {"count": 0, "taker_side": "yes"}])
check("trades with no taker or no size are dropped", flow.taker_flow("T") is None)
kalshi._get = boom
check("a missing tape endpoint is None, not a crash", flow.taker_flow("T") is None)

print("\nEPA from play-by-play")
HEAD = "posteam,defteam,epa,success,play_type,wp,week,season"
rows = [
    "KC,DEN,1.0,1,pass,0.5,1,2026",
    "KC,DEN,0.0,0,run,0.5,1,2026",
    "DEN,KC,-1.0,0,pass,0.5,1,2026",
    "KC,DEN,9.0,1,pass,0.99,1,2026",      # garbage time: must not count
    "KC,DEN,9.0,1,punt,0.5,1,2026",       # not a pass or a run
    "KC,DEN,,1,pass,0.5,1,2026",          # no epa
    "ZZZ,KC,1.0,1,pass,0.5,1,2026",       # an abbreviation we do not know
]
t = epa.nfl_from_csv(io.StringIO("\n".join([HEAD] + rows)), 2026)
kc = t["teams"]["Kansas City"]
check("only pass and run plays count", kc["off_plays"] == 2, str(kc["off_plays"]))
check("garbage time is excluded", kc["off_epa"] == 0.5, str(kc["off_epa"]))
check("offence and defence are both recorded",
      t["teams"]["Denver"]["def_epa"] == 0.5 and t["teams"]["Denver"]["off_plays"] == 1)
check("net is offence minus defence allowed",
      abs(kc["net_epa"] - (kc["off_epa"] - kc["def_epa"])) < 1e-9)
check("pass and run are split", kc["pass_epa"] == 1.0 and kc["rush_epa"] == 0.0)
check("an unknown abbreviation is reported, not dropped in silence",
      t["_unmapped"] == ["ZZZ"], str(t["_unmapped"]))

print("\nlast season fades on its own")
cur = epa.nfl_from_csv(io.StringIO("\n".join(
    [HEAD] + ["KC,DEN,1.0,1,pass,0.5,1,2026", "DEN,KC,0.0,0,pass,0.5,1,2026"])), 2026)
prior = epa.nfl_from_csv(io.StringIO("\n".join(
    [HEAD] + ["KC,DEN,-1.0,0,pass,0.5,1,2025",
              "DEN,KC,0.0,0,pass,0.5,1,2025"] * 100)), 2025)
b = epa.blend(cur, prior, 0.35)
kc = b["teams"]["Kansas City"]
check("one new play cannot outvote a season", kc["off_epa"] < 0, str(kc["off_epa"]))
check("the prior is recorded, not hidden", b["_prior"]["season"] == 2025)
check("this season's own count is kept", kc["this_season_plays"] == 1)
big = epa.nfl_from_csv(io.StringIO("\n".join(
    [HEAD] + ["KC,DEN,1.0,1,pass,0.5,1,2026", "DEN,KC,0.0,0,pass,0.5,1,2026"] * 500)), 2026)
kc2 = epa.blend(big, prior, 0.35)["teams"]["Kansas City"]
check("...and by 500 plays this season has taken over", kc2["off_epa"] > 0.8, str(kc2["off_epa"]))

print("\nturning EPA into a game")
table = {"teams": {
    "Good": {"net_epa": 0.20, "off_epa": 0.1, "def_epa": -0.1, "off_plays": 500, "def_plays": 500},
    "Bad": {"net_epa": -0.20, "off_epa": -0.1, "def_epa": 0.1, "off_plays": 500, "def_plays": 500},
    "Thin": {"net_epa": 0.9, "off_epa": 0.9, "def_epa": 0.0, "off_plays": 20, "def_plays": 20},
}}
m = epa.matchup(table, "Good", "Bad")
check("a big gap is shrunk toward the mean",
      abs(m["points"]) < abs(m["raw_points"]), f"{m['raw_points']} -> {m['points']}")
check("...and capped at three touchdowns", abs(m["points"]) <= epa.CAP_POINTS)
check("the favourite is favoured", m["p_home"] > 0.5)
back = epa.matchup(table, "Bad", "Good")
check("reversing the teams reverses the margin",
      abs(back["points"] + m["points"]) < 1e-9, f"{m['points']} vs {back['points']}")
check("home advantage moves the margin, in points",
      epa.matchup(table, "Good", "Bad", home_points=3)["points"]
      > m["points"] or abs(m["points"]) >= epa.CAP_POINTS)
check("a thin sample is refused", epa.matchup(table, "Thin", "Bad") is None)
check("an unrated team is refused", epa.matchup(table, "Nobody", "Bad") is None)
check("no table at all is refused", epa.matchup(None, "Good", "Bad") is None)
check("an even game is a coin flip", abs(epa._p_from_margin(0) - 0.5) < 1e-9)
check("a touchdown is worth about 70%", 0.66 < epa._p_from_margin(7) < 0.72,
      f"{epa._p_from_margin(7):.3f}")
check("the note talks points, not EPA", "by" in epa.note(m, "Good", "Bad")
      and "Good" in epa.note(m, "Good", "Bad"))

print("\ncollege needs a key, and says so rather than guessing")
check("no key means no table", epa.ncaaf_season(2026, key="") is None)
check("...and no table means no matchup", epa.matchup(None, "Texas", "Rice") is None)

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: " + ", ".join(FAIL)))
raise SystemExit(1 if FAIL else 0)
