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

Why all of this runs here and not on the phone: Kalshi refuses any request that
carries an Origin header. Measured, one variable at a time, from a runner:

    bot UA,     no Origin  -> 200        browser UA, no Origin  -> 200
    bot UA,   with Origin  -> 403        browser UA, with Origin -> 403
    OPTIONS preflight                    -> 403

That is not a missing CORS header, it is a refusal, so no page in a browser can
quote a price no matter how it asks. Live pricing therefore only exists where
this module runs. The app keeps your tickets locally at the last published
board price and grades them against the results the bot publishes; sending them
here is what gets them priced at the real book.
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


def settled_feed(led, results, path=SETTLED, keep=4000):
    """The results the phone grades its own tickets against.

    The app cannot ask Kalshi for a settlement, so the bot publishes the one
    thing it needs: which tickers won. This carries EVERY settled market the run
    saw, not only the ones in this ledger - a ticket you built on your phone and
    never sent here would otherwise contain legs nothing publishes a result for,
    and it would sit open for ever.

    Pruning keeps anything a ticket still refers to, then the most recently added
    of the rest (dicts keep insertion order, and new results are appended).
    """
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    out = dict(prev.get("results") or {})
    out.update(results)
    if len(out) > keep:
        wanted = {l["ticker"] for t in led["tickets"] for l in t["legs"]}
        held = {k: v for k, v in out.items() if k in wanted}
        spare = [(k, v) for k, v in out.items() if k not in wanted]
        room = max(0, keep - len(held))
        out = dict(list(held.items()) + spare[-room:])
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


# ------------------------------------------------------------------ the bot's
def bot_slate(legs, stake=STAKE, presets=None, **kw):
    """One ticket per rung, off the board as it stands right now."""
    import parlay
    out = []
    for r in parlay.ladder(legs, presets=presets, **kw):
        out.append(make_ticket(r["legs"], owner="bot", stake=stake,
                               key=r.get("key", "custom"), label=r.get("label", "Custom"),
                               emoji=r.get("emoji", "🎫"), target_payout=r.get("payout")))
    return out


def slate_from_tickers(tickers, legs, owner="you", stake=STAKE, label="My ticket",
                       key="custom", placed=None, source="live"):
    """Your ticket, re-priced against the live board rather than trusting the
    prices your phone showed. The phone's snapshot can be hours old; what you
    would actually have paid is what the book says when it is logged."""
    by_ticker = {l["ticker"]: l for l in legs}
    picked, missing = [], []
    for tk in tickers:
        tk = tk.strip()
        if not tk:
            continue
        if tk in by_ticker:
            picked.append(by_ticker[tk])
        else:
            missing.append(tk)
    if not picked:
        return None, missing
    return make_ticket(picked, owner=owner, stake=stake, key=key, label=label,
                       emoji="🎟️", placed=placed, source=source), missing


def _report(led):
    s = summary(led)
    for who, a in sorted(s["by_owner"].items()):
        print(f"[paper] {who:<4} {a['settled']:>3} settled  {a['won']}-{a['lost']}"
              f"  staked {a['staked']:.2f}  back {a['returned']:.2f}"
              f"  pnl {a['pnl']:+.2f} ({a['roi']:+.1f}%)"
              f"  | open {a['open']} worth {a['open_value']:.2f} ({a['open_pnl']:+.2f})"
              f"  | expected {a['implied_wins']:.2f} wins, got {a['won']}")
    for k, a in sorted(s["by_rung"].items(), key=lambda kv: -kv[1]["settled"]):
        if a["settled"]:
            print(f"[paper]   {k:<9} {a['won']}-{a['lost']} of {a['settled']}"
                  f"  expected {a['implied_wins']:.2f}  pnl {a['pnl']:+.2f}")
    return s


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Paper parlays at live Kalshi prices")
    ap.add_argument("--place", action="store_true", help="buy the bot a ticket per rung")
    ap.add_argument("--mark", action="store_true", help="re-price open tickets at the book")
    ap.add_argument("--grade", action="store_true", help="settle what the markets have settled")
    ap.add_argument("--days", type=int, default=8, help="board window for placing")
    ap.add_argument("--scope", default="football", help="which board the bot buys from")
    ap.add_argument("--stake", type=float, default=STAKE)
    ap.add_argument("--back-days", type=int, default=14, help="how far back to look for settlements")
    ap.add_argument("--add", default="", help="log a ticket from comma/newline separated tickers")
    ap.add_argument("--owner", default="you", help="who the --add ticket belongs to")
    ap.add_argument("--label", default="My ticket")
    ap.add_argument("--cash-out", default="", help="close an open ticket by id, at its last mark")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)

    import parlay
    led = load_ledger(a.ledger)
    cfg = parlay.load_config()
    need_board = a.place or a.mark or a.add
    legs = []
    if need_board:
        kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
        legs = parlay.fetch(cfg, a.days)
        print(f"[paper] board: {len(legs)} legs live")

    if a.grade:
        since = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(days=a.back_days)).timestamp()
        # every settled market, not only the ones this ledger knows about: the
        # phone grades tickets that were never sent here, and it can only do
        # that from what gets published
        wanted = {l["ticker"] for t in open_tickets(led) for l in t["legs"]
                  if l["result"] is None}
        results = {}
        for key in parlay.all_leagues(cfg):
            spec = parlay.league_spec(cfg, key)
            if not spec:
                continue
            try:
                evs = kalshi.settled_events(spec["ticker"], since, spec["ticker_order"])
            except Exception as e:
                print(f"[paper] {key} settlements failed: {type(e).__name__}: {str(e)[:70]}")
                continue
            got = results_from_events(evs)
            mine = len(wanted & set(got))
            print(f"[paper] {key}: {len(got)} settled markets"
                  + (f", {mine} of them ours" if mine else ""))
            results.update(got)
        n = grade(led, {k: v for k, v in results.items() if k in wanted})
        print(f"[paper] graded {n} tickets")
        print(f"[paper] published {settled_feed(led, results)} settled results for the app")

    if a.add and legs:
        t, missing = slate_from_tickers(a.add.replace("\n", ",").split(","), legs,
                                        owner=a.owner, stake=a.stake, label=a.label)
        if missing:
            print(f"[paper] not on the board, skipped: {', '.join(missing[:6])}")
        if not t:
            print("[paper] nothing to log - none of those tickers are trading")
        elif add(led, t):
            print(f"[paper] logged {t['id']}: {len(t['legs'])} legs, "
                  f"pays {t['entry']['multiple']:.2f}x, wins {100*t['entry']['win_prob']:.1f}%")
        else:
            print("[paper] you already have that exact ticket open")

    if a.cash_out:
        hit = [t for t in led["tickets"] if t["id"] == a.cash_out]
        if not hit:
            print(f"[paper] no ticket {a.cash_out}")
        elif cash_out(hit[0]):
            print(f"[paper] cashed {hit[0]['id']} for {hit[0]['returned']:.2f} "
                  f"({hit[0]['pnl']:+.2f})")
        else:
            print("[paper] that ticket is not open, or has never been marked")

    if a.place and legs:
        book = parlay.scope_legs(legs, a.scope)
        bought = 0
        for t in bot_slate(book, stake=a.stake):
            if add(led, t):
                bought += 1
                print(f"[paper] bot bought {t['label']:<9} {len(t['legs']):>2} legs  "
                      f"pays {t['entry']['multiple']:>7.2f}x  "
                      f"wins {100*t['entry']['win_prob']:>5.2f}%")
        print(f"[paper] {bought} new tickets ({len(open_tickets(led))} open in total)")

    # marked last, so a ticket bought a moment ago already shows what selling it
    # would fetch - which is the spread it just crossed, and worth seeing
    if a.mark and legs:
        print(f"[paper] marked {mark_to_market(led, quote_map(legs))} open tickets to the book")

    cal = save_calibration(led)
    s = _report(led)
    out = os.path.dirname(os.path.abspath(a.ledger))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, sort_keys=True)
    save_ledger(led, a.ledger)
    # one small file for the phone: the ledger itself is thousands of tickets,
    # and the app only ever shows what is open and what closed recently
    app = parlay.load_paper(out)
    if app:
        with open(os.path.join(out, "app.json"), "w", encoding="utf-8") as f:
            json.dump(app, f, separators=(",", ":"), sort_keys=True)
        print(f"[paper] app feed: {len(app['open'])} open, {len(app['closed'])} closed")
    learned = cal.get("learned_discount") or {}
    print(f"[paper] {cal['legs_settled']} legs settled; "
          + (f"calibration now steering the builder: {learned}" if learned
             else f"no band has {MIN_BAND_N} settled legs yet, so the hand-set curve still stands"))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
