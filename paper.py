#!/usr/bin/env python3
"""Paper parlays - the bot's and yours - priced at the live book and graded when
the games settle.

Three things happen here, and they are deliberately separate:

  place   the bot buys one ticket per rung at the prices on the book right now,
          and writes down what it paid. Entry price is a fact, recorded once.
  mark    every open ticket is re-priced against the current book, so you can
          see what it is worth before it settles and sell it if you want to.
  grade   once the markets settle, each leg gets a result and the ticket is
          won, lost or voided, with the money worked out from the entry price.

A parlay's value at any moment is the product of its legs' prices: buying it
cost `prod(ask)` per dollar of payout, and it is worth `prod(bid)` if you sold
every leg now. That is the whole model - there is no separate parlay market to
quote, so a mark is what the legs are actually bid at.

The ledger is append-only JSON. Nothing here ever deletes a ticket: a wrong
ticket is part of the record, which is the point of paper trading.
"""
import datetime as dt
import json
import os

import kalshi

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "v2", "paper", "ledger.json")
CALIBRATION = os.path.join(HERE, "data", "v2", "paper", "calibration.json")
SETTLED = os.path.join(HERE, "data", "v2", "settled.json")

STAKE = 10.0
# Price bands the calibration is measured in. They match discount()'s bands in
# parlay.py, because the whole point of measuring is to replace those numbers.
BANDS = [(0.90, 1.01, "90c+"), (0.60, 0.90, "60-90c"),
         (0.40, 0.60, "40-60c"), (0.00, 0.40, "under 40c")]
# How many settled legs a band needs before its measured number is trusted over
# the hand-set curve. 60 is where a hit rate stops swinging on one upset.
MIN_BAND_N = 60
MAX_TICKETS = 4000          # the ledger is a file in a repo, not a database


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def band_of(price):
    for lo, hi, name in BANDS:
        if lo <= price < hi:
            return name
    return BANDS[-1][2]


# ------------------------------------------------------------------- the ledger
def load_ledger(path=LEDGER):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "updated_utc": "", "tickets": []}
    d.setdefault("tickets", [])
    d.setdefault("version", 1)
    return d


def save_ledger(led, path=LEDGER):
    led["updated_utc"] = now()
    # oldest first, capped: a settled ticket from March teaches nothing the
    # calibration file has not already absorbed
    led["tickets"] = led["tickets"][-MAX_TICKETS:]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(led, f, separators=(",", ":"), sort_keys=True)
    return path


def ticket_id(owner, key, placed):
    stamp = placed.replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
    return f"{owner[0]}-{stamp}-{key}"


def cost_of(legs, field="entry_ask"):
    """What one dollar of payout cost: the product of the leg prices."""
    c = 1.0
    for l in legs:
        c *= max(float(l[field]), 1e-9)
    return c


def win_prob_of(legs, field="entry_p"):
    p = 1.0
    for l in legs:
        p *= max(float(l.get(field) or 0.0), 0.0)
    return p


def make_ticket(legs, owner="bot", stake=STAKE, key="custom", label="Custom",
                emoji="🎫", target_payout=None, placed=None, source="live"):
    """A ticket at today's prices. `legs` are parlay.py legs straight off the board."""
    placed = placed or now()
    mine = []
    for l in legs:
        mine.append({
            "ticker": l["ticker"], "league": l.get("league", ""),
            "event_id": l.get("event_id", ""), "pick": l.get("pick", ""),
            "opp": l.get("opp", ""), "date": l.get("date", ""),
            "entry_ask": round(float(l["ask"]), 4),
            "entry_bid": round(float(l["bid"]), 4) if l.get("bid") is not None else None,
            "entry_p": round(float(l.get("p") or l["ask"]), 4),
            "result": None, "mark_bid": None,
        })
    cost = cost_of(mine)
    return {
        "id": ticket_id(owner, key, placed),
        "owner": owner, "key": key, "label": label, "emoji": emoji,
        "placed_utc": placed, "stake": round(float(stake), 2),
        "target_payout": target_payout,
        "price_source": source,          # "live" = quoted off the book at placement
        "entry": {"cost": round(cost, 6),
                  "multiple": round(1.0 / cost, 4) if cost > 0 else 0.0,
                  "win_prob": round(win_prob_of(mine), 6)},
        "legs": mine,
        "status": "open",
        "mark": None, "closed_utc": None, "exit": None,
        "returned": None, "pnl": None,
    }


def open_tickets(led):
    return [t for t in led["tickets"] if t["status"] == "open"]


def same_ticket(a, b):
    return (a["owner"] == b["owner"]
            and sorted(l["ticker"] for l in a["legs"]) == sorted(l["ticker"] for l in b["legs"]))


def add(led, ticket):
    """Adds unless an identical open ticket for the same owner already exists.

    Without this the daily run would re-buy the same Banker every morning until
    Saturday and the record would be six copies of one bet."""
    for t in open_tickets(led):
        if same_ticket(t, ticket):
            return None
    led["tickets"].append(ticket)
    return ticket


# -------------------------------------------------------------------- pricing
def quote_map(legs):
    """ticker -> live quote, from a board pull that already happened."""
    return {l["ticker"]: {"ask": l.get("ask"), "bid": l.get("bid"), "p": l.get("p")}
            for l in legs if l.get("ticker")}


def mark_to_market(led, quotes, stamp=None):
    """What every open ticket is worth right now, at what the legs are bid.

    Selling a parlay means selling each leg, so the exit price is the product of
    the bids - always below the product of the asks you paid, which is why a
    ticket shows a loss the instant it is bought. That is the spread, not a bug.
    """
    stamp = stamp or now()
    touched = 0
    for t in open_tickets(led):
        bids, seen = [], 0
        for l in t["legs"]:
            q = quotes.get(l["ticker"])
            if q and q.get("bid") is not None:
                l["mark_bid"] = round(float(q["bid"]), 4)
                seen += 1
            # a leg that has left the board (kicked off, settled) holds its last mark
            bids.append(l["mark_bid"] if l["mark_bid"] is not None else l["entry_ask"])
        if not seen:
            continue
        value = 1.0
        for b in bids:
            value *= max(float(b), 1e-9)
        # value/cost is what the same payout would fetch now against what it cost
        ratio = value / max(t["entry"]["cost"], 1e-9)
        t["mark"] = {"utc": stamp, "legs_quoted": seen,
                     "value": round(t["stake"] * ratio, 2),
                     "pnl": round(t["stake"] * (ratio - 1.0), 2)}
        touched += 1
    return touched


def cash_out(t, stamp=None):
    """Close an open ticket at its last mark. Kalshi has no parlay to sell, so
    this is the honest equivalent: sell every leg at the bid."""
    if t["status"] != "open" or not t.get("mark"):
        return False
    t["status"] = "cashed"
    t["closed_utc"] = stamp or now()
    t["exit"] = dict(t["mark"])
    t["returned"] = t["mark"]["value"]
    t["pnl"] = round(t["returned"] - t["stake"], 2)
    return True


# -------------------------------------------------------------------- grading
def results_from_events(evs):
    """ticker -> 'win' | 'loss' | 'void', from settled Kalshi events.

    A voided event settles every side NO, so 'all sides lost' is void, not a
    clean sweep of losses - grading it as losses would invent a result.
    """
    out = {}
    for ev in evs:
        sides = kalshi.match_sides(ev)
        if not sides or not all(s.get("settled") for s in sides):
            continue
        void = not any(s.get("won") for s in sides)
        for s in sides:
            out[s["ticker"]] = "void" if void else ("win" if s.get("won") else "loss")
    return out


def grade(led, results, stamp=None):
    """Settle what can be settled. A ticket is lost the moment one leg loses;
    it is won only when every leg has and they all won.

    A voided leg is dropped from the ticket the way a book drops it: the payout
    shrinks to what the remaining legs make. Everything is worked out from the
    entry price, never from a later quote.
    """
    stamp = stamp or now()
    settled = 0
    for t in open_tickets(led):
        for l in t["legs"]:
            if l["result"] is None and l["ticker"] in results:
                l["result"] = results[l["ticker"]]
        live = [l for l in t["legs"] if l["result"] != "void"]
        if any(l["result"] == "loss" for l in live):
            t["status"] = "lost"
        elif live and all(l["result"] == "win" for l in live):
            t["status"] = "won"
        elif not live and t["legs"] and all(l["result"] == "void" for l in t["legs"]):
            t["status"] = "void"
        else:
            continue                       # still has legs in play
        t["closed_utc"] = stamp
        if t["status"] == "won":
            mult = 1.0 / max(cost_of(live), 1e-9)
            t["returned"] = round(t["stake"] * mult, 2)
        elif t["status"] == "void":
            t["returned"] = t["stake"]     # every leg voided: stake back
        else:
            t["returned"] = 0.0
        t["pnl"] = round(t["returned"] - t["stake"], 2)
        settled += 1
    return settled


def settled_feed(led, results, path=SETTLED, keep=1200):
    """The results the phone grades its own tickets against.

    The app cannot call Kalshi for a settlement, so the bot publishes the one
    thing it needs: which tickers won. Only tickers that appear in someone's
    paper ticket are worth carrying, plus whatever is already published.
    """
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    out = dict(old.get("results") or {})
    out.update(results)
    if len(out) > keep:
        wanted = {l["ticker"] for t in led["tickets"] for l in t["legs"]}
        trimmed = {k: v for k, v in out.items() if k in wanted}
        out = trimmed if trimmed else dict(list(out.items())[-keep:])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_utc": now(), "results": out}, f,
                  separators=(",", ":"), sort_keys=True)
    return len(out)


# ---------------------------------------------------------------- what it says
def _blank():
    return {"open": 0, "settled": 0, "won": 0, "lost": 0, "void": 0, "cashed": 0,
            "staked": 0.0, "returned": 0.0, "pnl": 0.0, "roi": 0.0,
            "implied_wins": 0.0, "open_stake": 0.0, "open_value": 0.0}


def _add(acc, t):
    if t["status"] == "open":
        acc["open"] += 1
        acc["open_stake"] += t["stake"]
        acc["open_value"] += (t.get("mark") or {}).get("value") or t["stake"]
        return
    acc["settled"] += 1
    acc[t["status"] if t["status"] in ("won", "lost", "void", "cashed") else "lost"] += 1
    acc["staked"] += t["stake"]
    acc["returned"] += t.get("returned") or 0.0
    acc["implied_wins"] += t["entry"].get("win_prob") or 0.0


def _finish(acc):
    for k in ("staked", "returned", "open_stake", "open_value"):
        acc[k] = round(acc[k], 2)
    acc["pnl"] = round(acc["returned"] - acc["staked"], 2)
    acc["roi"] = round(100.0 * acc["pnl"] / acc["staked"], 1) if acc["staked"] else 0.0
    acc["implied_wins"] = round(acc["implied_wins"], 2)
    acc["open_pnl"] = round(acc["open_value"] - acc["open_stake"], 2)
    return acc


def summary(led):
    """The scoreboard: you against the bot, and each rung against its own odds.

    `implied_wins` is the sum of the entry win probabilities - what these exact
    tickets should have won on average. Comparing it with `won` is the only
    honest read on a handful of parlays, because one 40x ticket landing makes
    any win rate look like genius.
    """
    owners, rungs = {}, {}
    for t in led["tickets"]:
        _add(owners.setdefault(t["owner"], _blank()), t)
        if t["owner"] == "bot":
            _add(rungs.setdefault(t.get("key") or "custom", _blank()), t)
    closed = [t for t in led["tickets"] if t["status"] != "open"]
    closed.sort(key=lambda t: t.get("closed_utc") or "")
    return {
        "generated_utc": now(),
        "by_owner": {k: _finish(v) for k, v in owners.items()},
        "by_rung": {k: _finish(v) for k, v in rungs.items()},
        "tickets": len(led["tickets"]),
        "recent": [{"id": t["id"], "owner": t["owner"], "label": t["label"],
                    "status": t["status"], "n": len(t["legs"]),
                    "multiple": t["entry"]["multiple"], "stake": t["stake"],
                    "returned": t.get("returned"), "pnl": t.get("pnl"),
                    "closed_utc": t.get("closed_utc")}
                   for t in closed[-12:][::-1]],
    }


def calibration(led):
    """What the settled legs say about the prices they were bought at.

    For each price band: how often those legs actually won, against what they
    cost. `drag` is hit rate over mean price - 1.0 means the price was right,
    below 1.0 means that band is worse than it looks. It is measured on entry
    prices only, so it cannot be polluted by a later quote.
    """
    bands = {name: {"n": 0, "wins": 0, "sum_price": 0.0} for _, _, name in BANDS}
    rungs = {}
    legs_seen = 0
    for t in led["tickets"]:
        for l in t["legs"]:
            if l["result"] not in ("win", "loss"):
                continue
            b = bands[band_of(l["entry_ask"])]
            b["n"] += 1
            b["wins"] += 1 if l["result"] == "win" else 0
            b["sum_price"] += l["entry_ask"]
            legs_seen += 1
        if t["owner"] == "bot" and t["status"] in ("won", "lost"):
            r = rungs.setdefault(t.get("key") or "custom",
                                 {"n": 0, "won": 0, "implied": 0.0})
            r["n"] += 1
            r["won"] += 1 if t["status"] == "won" else 0
            r["implied"] += t["entry"].get("win_prob") or 0.0
    out_bands = {}
    for name, b in bands.items():
        if not b["n"]:
            continue
        mean = b["sum_price"] / b["n"]
        hit = b["wins"] / b["n"]
        out_bands[name] = {"n": b["n"], "wins": b["wins"],
                           "hit": round(hit, 4), "mean_entry": round(mean, 4),
                           "drag": round(hit / mean, 4) if mean else 0.0,
                           "trusted": b["n"] >= MIN_BAND_N}
    for r in rungs.values():
        r["implied"] = round(r["implied"] / r["n"], 4) if r["n"] else 0.0
        r["actual"] = round(r["won"] / r["n"], 4) if r["n"] else 0.0
    return {"generated_utc": now(), "legs_settled": legs_seen,
            "min_band_n": MIN_BAND_N, "bands": out_bands, "rungs": rungs}


def learned_discount(cal, floor=0.85):
    """The measured drag per band, for the bands that have earned the right to
    replace the hand-set curve.

    Capped at 1.0 deliberately: a band that measured *better* than its price is
    a small sample telling you that you are beating the market, and building on
    that belief is how a paper record turns into a real loss. Floored so one bad
    weekend cannot make the builder refuse a whole price band.
    """
    out = {}
    for name, b in (cal.get("bands") or {}).items():
        if b.get("trusted"):
            out[name] = round(max(floor, min(1.0, b["drag"])), 4)
    return out


def save_calibration(led, path=CALIBRATION):
    cal = calibration(led)
    cal["learned_discount"] = learned_discount(cal)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=1, sort_keys=True)
    return cal
