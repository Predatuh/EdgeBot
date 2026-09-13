"""Offline checks for the parlay backtest. Run: python test_backtest.py

The one that matters is the leakage guard. A backtest that accidentally prices off
a settled market reports every ticket as a winner and is worse than no backtest,
because it looks like evidence.
"""
import datetime as dt
import sys

import backtest
import kalshi
import parlay

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


CUTOFF = dt.datetime(2026, 9, 11, 13, 0, tzinfo=dt.timezone.utc)
TODAY = CUTOFF.date().isoformat()


def fake_settled(favourite_won=True, n=6):
    """Games whose SETTLED prices scream the answer: 99c winner, 1c loser."""
    evs = []
    for i in range(n):
        evs.append({"event": f"G{i}", "date": TODAY, "close": "", "sides": [
            {"name": f"Fav{i}", "ticker": f"T{i}-F", "code": f"F{i}", "reg": False,
             "won": favourite_won, "settled": True, "settle_price": 0.99,
             "is_tie": False, "home": True},
            {"name": f"Dog{i}", "ticker": f"T{i}-D", "code": f"D{i}", "reg": False,
             "won": not favourite_won, "settled": True, "settle_price": 0.01,
             "is_tie": False, "home": False},
        ]})
    return evs


def install(evs, quotes, leagues=("nfl",)):
    def stub(series, ticker, ts, **kw):
        q = quotes.get(ticker)
        return None if not q else {"bid": q[0], "ask": q[1], "mid": q[2], "ts": ts - 3600,
                                   "vol": 900, "oi": 4000}
    kalshi.settled_events = lambda series, ts=None, order="away_home": list(evs)
    kalshi.quote_at = stub
    parlay.all_leagues = lambda cfg: list(leagues)


# ---------------------------------------------------------------- candle parsing
# The first live run priced 0 of 665 sides and raised nothing, because Kalshi
# returns dollar STRINGS under <field>_dollars and the parser asked for `close`.
print("\ncandle parsing")
REAL = {"end_period_ts": 1789131600, "volume_fp": "2521.46",
        "price": {"close_dollars": "0.9900", "open_dollars": "0.9900"},
        "yes_bid": {"close_dollars": "0.9900", "open_dollars": "0.9900"},
        "yes_ask": {"close_dollars": "1.0000", "open_dollars": "1.0000"}}
check("a dollar string parses to a probability",
      kalshi._candle_price(REAL["yes_bid"]) == 0.99,
      str(kalshi._candle_price(REAL["yes_bid"])))
check("the ask side too", kalshi._candle_price(REAL["yes_ask"]) == 1.0)
check("integer cents still work (older payloads)",
      kalshi._candle_price({"close": 47}) == 0.47)
check("a missing book is not a price", kalshi._candle_price(None) is None)
check("an unparseable string is not a price",
      kalshi._candle_price({"close_dollars": "n/a"}) is None)
check("an empty book is not a price", kalshi._candle_price({}) is None)
check("a candle timestamp is read", kalshi._candle_ts(REAL) == 1789131600)
check("a candle with NO readable timestamp is rejected, not defaulted",
      kalshi._candle_ts({"yes_bid": {"close_dollars": "0.50"}}) is None)

# quote_at must never hand back a quote from after the cutoff.
CUT_TS = 1789131600
def fake_candles(rows):
    kalshi.candles = lambda series, ticker, a, b, period=60: rows
fake_candles([
    {"end_period_ts": CUT_TS - 7200, "yes_bid": {"close_dollars": "0.60"}, "yes_ask": {"close_dollars": "0.62"}},
    {"end_period_ts": CUT_TS - 3600, "yes_bid": {"close_dollars": "0.64"}, "yes_ask": {"close_dollars": "0.66"}},
    {"end_period_ts": CUT_TS + 3600, "yes_bid": {"close_dollars": "0.99"}, "yes_ask": {"close_dollars": "1.00"}},
])
q = kalshi.quote_at("S", "T", CUT_TS)
check("quote_at takes the latest candle at or before the cutoff",
      q and abs(q["bid"] - 0.64) < 1e-9 and abs(q["ask"] - 0.66) < 1e-9, str(q))
check("...and never the one after it, however tempting",
      q and q["ts"] == CUT_TS - 3600 and q["ask"] < 0.9, str(q))
fake_candles([{"end_period_ts": CUT_TS + 60, "yes_bid": {"close_dollars": "0.99"},
               "yes_ask": {"close_dollars": "1.00"}}])
check("a market only quoted after the cutoff yields nothing",
      kalshi.quote_at("S", "T", CUT_TS) is None)
fake_candles([{"yes_bid": {"close_dollars": "0.99"}, "yes_ask": {"close_dollars": "1.00"}}])
check("an undated candle is discarded, not trusted",
      kalshi.quote_at("S", "T", CUT_TS) is None)
fake_candles([{"end_period_ts": CUT_TS - 60, "yes_bid": {"close_dollars": "0.80"},
               "yes_ask": {"close_dollars": "0.20"}}])
check("a crossed book is discarded", kalshi.quote_at("S", "T", CUT_TS) is None)


# ---------------------------------------------------------------- leakage guard
print("\nleakage guard")
# Every market settled at 99c/1c, but on Friday morning they were coin flips.
coinflips = {}
for i in range(6):
    coinflips[f"T{i}-F"] = (0.49, 0.51, 0.50)
    coinflips[f"T{i}-D"] = (0.49, 0.51, 0.50)
install(fake_settled(True), coinflips)
cfg = {"leagues": {"nfl": {"ticker": "KXNFLGAME", "label": "NFL", "ticker_order": "away_home"}}}
legs, winners = backtest.board_at(cfg, CUTOFF, 3)

check("a leg is priced off the historical quote, not the settlement",
      legs and all(abs(l["p"] - 0.50) < 1e-6 for l in legs),
      str(sorted({round(l["p"], 3) for l in legs})))
check("...and no leg inherits the 99c settlement price",
      not any(l["p"] > 0.9 or l["ask"] > 0.9 for l in legs))
check("outcomes are still read correctly for grading",
      all(winners[f"T{i}-F"] and not winners[f"T{i}-D"] for i in range(6)))
check("backtest.py never mentions settle_price",
      "settle_price" not in open("backtest.py").read().split('"""', 2)[2])

# A market with no quote before the cutoff was not buyable then.
install(fake_settled(True), dict(list(coinflips.items())[:2]))
legs2, _ = backtest.board_at(cfg, CUTOFF, 3)
check("a game only half-quoted at the cutoff is dropped whole", len(legs2) == 2,
      f"{len(legs2)} legs")
install(fake_settled(True), {})
legs3, _ = backtest.board_at(cfg, CUTOFF, 3)
check("a board with no quotes yields nothing, not a guess", legs3 == [])

# ---------------------------------------------------------------- grading
print("\ngrading")
lg = [dict(parlay.legs_from_events([{
    "event": "G9", "date": TODAY, "close": "", "sides": [
        {"name": "A", "ticker": "WIN", "prob": 0.9, "ask": 0.92, "bid": 0.90,
         "is_tie": False, "home": True, "vol": 0, "oi": 0},
        {"name": "B", "ticker": "LOSE", "prob": 0.1, "ask": 0.12, "bid": 0.10,
         "is_tie": False, "home": False, "vol": 0, "oi": 0}]}], "nfl", "NFL")[0])]
t = parlay.summarise(lg)
check("a ticket whose legs all won is a winner",
      backtest.grade(t, {"WIN": True})["won"])
check("one losing leg loses the ticket", not backtest.grade(t, {"WIN": False})["won"])
check("a leg with no recorded result counts as a loss, never a win",
      not backtest.grade(t, {})["won"])
g = backtest.grade(parlay.summarise(lg * 1), {"WIN": False})
check("the killer is named", g["killers"] and g["killers"][0]["pick"] == "A")

# ---------------------------------------------------------------- accounting
print("\naccounting")
# Six games with real favourite prices, all of which won. The coin-flip fixture
# above deliberately cannot build a ticket - every preset floors at 85c.
favprices = {}
for i in range(6):
    favprices[f"T{i}-F"] = (0.955, 0.965, 0.960)
    favprices[f"T{i}-D"] = (0.035, 0.045, 0.040)
install(fake_settled(True), favprices)
res = backtest.run(CUTOFF, 3, stake=10.0)
s = res["summary"]
check("tickets were built", s["n"] > 0, str(s))
check("staked is stake x tickets", abs(s["staked"] - 10.0 * s["n"]) < 1e-6)
check("returned is the sum of the winners' payouts",
      abs(s["returned"] - sum(t["returned"] for t in res["tickets"])) < 1e-6)
check("profit reconciles", abs(s["profit"] - (s["returned"] - s["staked"])) < 1e-6)
check("roi reconciles", s["staked"] > 0 and abs(s["roi"] - s["profit"] / s["staked"]) < 1e-4,
      f'{s["roi"]} vs {s["profit"] / s["staked"] if s["staked"] else 0}')
check("a losing ticket returns nothing",
      all(t["returned"] == 0.0 for t in res["tickets"] if not t["won"]))
check("a winning ticket returns stake x multiple",
      all(abs(t["returned"] - 10.0 * t["multiple"]) < 0.01 for t in res["tickets"] if t["won"]))
check("expected winners is the sum of the ticket probabilities",
      abs(s["expected_won"] - round(sum(t["win_prob"] for t in res["tickets"]), 2)) < 0.01)
check("no two tickets anywhere are the same bet",
      len({tuple(sorted(l["ticker"] for l in t["legs"])) for t in res["tickets"]})
      == len(res["tickets"]))
check("a bet reachable from several scopes is counted once and says so",
      all(isinstance(t["also_via"], list) for t in res["tickets"]))
check("every leg carries its own result",
      all("won" in l for t in res["tickets"] for l in t["legs"]))
check("correlation is reported, not hidden",
      res["summary"]["distinct_legs"] > 0 and res["summary"]["most_reused_leg"] >= 1)

# A postponed game settles NO on every side. Scoring that as a loss would invent
# losses the bettor never took.
void_evs = [{"event": "V1", "date": TODAY, "close": "", "sides": [
    {"name": "A", "ticker": "V-A", "code": "A", "reg": False, "won": False, "settled": True,
     "settle_price": 0.0, "is_tie": False, "home": True},
    {"name": "B", "ticker": "V-B", "code": "B", "reg": False, "won": False, "settled": True,
     "settle_price": 0.0, "is_tie": False, "home": False}]}]
install(void_evs, {"V-A": (0.95, 0.96, 0.955), "V-B": (0.03, 0.05, 0.04)})
vlegs, vwin = backtest.board_at(cfg, CUTOFF, 3)
check("a voided game produces no legs at all", vlegs == [], str(vlegs))
check("...and no phantom result", vwin == {})

# Every favourite won, so a ticket made only of favourites must have cashed.
favs = [t for t in res["tickets"] if all(l["pick"].startswith("Fav") for l in t["legs"])]
check("tickets built entirely from winners all cashed",
      favs and all(t["won"] for t in favs), f"{sum(1 for t in favs if t['won'])}/{len(favs)}")

# Flip the result: identical prices, opposite outcomes, so nothing built on Fav can win.
install(fake_settled(False), favprices)
flipped = backtest.run(CUTOFF, 3, stake=10.0)
check("flipping the outcomes flips the winners",
      flipped["summary"]["won"] != s["won"] or s["won"] == 0,
      f"{s['won']} vs {flipped['summary']['won']}")
check("...while the prices stay identical",
      abs(flipped["summary"]["expected_won"] - s["expected_won"]) < 0.01,
      f"{s['expected_won']} vs {flipped['summary']['expected_won']}")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: " + ", ".join(FAILED)))
sys.exit(1 if FAILED else 0)
