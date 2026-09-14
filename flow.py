#!/usr/bin/env python3
"""What the market did before we got here: price history, and who is pushing it.

Two questions, both answered from data we already have access to.

  Where has the price been?  Kalshi's candlesticks give a bid/ask every minute
  back to when the market listed. Comparing where a side opened with where it is
  now is the exchange's version of line movement, and movement is the one thing
  in betting markets that reliably carries information: closing prices beat
  opening prices, so a side the market is moving TOWARD has money behind it.

  Who is pushing it?  /markets/trades gives every print with its size and which
  side took it. Count the trades and you get "tickets"; add up the contracts and
  you get "money". When 80% of the trades are buying one side but only 45% of the
  contracts are, the small bets and the big bets disagree - and that gap is what
  a sportsbook's ticket-versus-money split is measuring.

Neither of these is used to override a pick. They are recorded on it, shown, and
graded, so that whether they actually predict anything is a question the record
answers rather than one I answer here.
"""
import datetime as dt

import kalshi

LOOKBACK_H = 96          # far enough back to catch a market that listed on Monday
PERIOD_MIN = 60
STEAM = 0.03             # 3c is the smallest move worth calling a move
BIG_TRADE = 100          # contracts; at this size it is not a retail click


def history(series, ticker, hours=LOOKBACK_H, period=PERIOD_MIN, now=None):
    """Every readable quote for this market, oldest first.

    Reuses the candle parsing the backtest already proved out - including the
    part where Kalshi returns prices as dollar strings, which silently yields
    None if you read them as cents.
    """
    now = int(now or dt.datetime.now(dt.timezone.utc).timestamp())
    try:
        cs = kalshi.candles(series, ticker, now - hours * 3600, now, period)
    except Exception as e:
        print(f"[flow] {ticker}: no history ({type(e).__name__}: {str(e)[:60]})")
        return []
    out = []
    for c in cs:
        ts = kalshi._candle_ts(c)
        bid = kalshi._candle_price(c.get("yes_bid"))
        ask = kalshi._candle_price(c.get("yes_ask"))
        if ts is None or bid is None or ask is None:
            continue
        if not (0 <= bid <= ask <= 1):
            continue
        out.append({"ts": ts, "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
                    "vol": kalshi._f(c.get("volume_fp")) or kalshi._f(c.get("volume")) or 0.0,
                    "oi": kalshi._f(c.get("open_interest_fp")) or kalshi._f(c.get("open_interest")) or 0.0})
    out.sort(key=lambda x: x["ts"])
    return out


def move(hist):
    """Where this side opened, where it is now, and how far it travelled."""
    if len(hist) < 2:
        return None
    first, last = hist[0], hist[-1]
    mids = [h["mid"] for h in hist]
    return {
        "open": round(first["mid"], 4),
        "now": round(last["mid"], 4),
        "delta": round(last["mid"] - first["mid"], 4),
        "high": round(max(mids), 4),
        "low": round(min(mids), 4),
        "hours": round((last["ts"] - first["ts"]) / 3600.0, 1),
        "points": len(hist),
    }


def _size(t):
    """Contracts on a trade.

    It arrives as `count_fp`, a decimal STRING - the same shape that made the
    first backtest price zero of 665 sides when it read candles as cents. Reading
    `count` instead returns nothing, silently, and the whole tape reads as empty.
    """
    for field in ("count_fp", "count"):
        v = kalshi._f(t.get(field))
        if v:
            return v
    return 0.0


def taker_flow(ticker, limit=500, big=BIG_TRADE):
    """Tickets versus money, from the tape.

    `taker_side` is whoever crossed the spread to get filled - the aggressor, and
    the side with an opinion. A resting order that gets hit is not making the
    argument; the taker is.
    """
    try:
        js = kalshi._get("/markets/trades", {"ticker": ticker, "limit": limit})
    except Exception as e:
        print(f"[flow] {ticker}: no tape ({type(e).__name__}: {str(e)[:60]})")
        return None
    trades = js.get("trades") or []
    if not trades:
        return None
    n_yes = n_no = c_yes = c_no = 0
    sizes, big_yes, big_no, blocks, foreign = [], 0, 0, 0, 0
    for t in trades:
        # The API ignores an unrecognised filter and answers with the WHOLE
        # exchange's tape, which reads exactly like a busy market for the ticker
        # asked about. Checking the ticker back is the difference between a
        # signal and a confident fiction.
        if t.get("ticker") and t["ticker"] != ticker:
            foreign += 1
            continue
        cnt = _size(t)
        side = (t.get("taker_side") or t.get("taker_outcome_side") or "").lower()
        if side not in ("yes", "no") or cnt <= 0:
            continue
        sizes.append(cnt)
        blocks += 1 if t.get("is_block_trade") else 0
        if side == "yes":
            n_yes += 1
            c_yes += cnt
            big_yes += cnt if cnt >= big else 0
        else:
            n_no += 1
            c_no += cnt
            big_no += cnt if cnt >= big else 0
    if foreign:
        print(f"[flow] {ticker}: {foreign} trades came back for other markets; ignored")
    n, c = n_yes + n_no, c_yes + c_no
    if not n or not c:
        return None
    sizes.sort()
    return {
        "trades": n, "contracts": round(c, 1),
        "ticket_share": round(n_yes / n, 4),        # share of PRINTS buying this side
        "money_share": round(c_yes / c, 4),         # share of CONTRACTS buying it
        "gap": round(c_yes / c - n_yes / n, 4),     # + = the big money agrees with the crowd
        "median_size": round(sizes[len(sizes) // 2], 1),
        "max_size": round(sizes[-1], 1),
        "blocks": blocks,
        "big_share": round((big_yes + big_no) / c, 4),
        "big_money_share": round(big_yes / (big_yes + big_no), 4) if (big_yes + big_no) else None,
    }


def book(ticker, near=0.05):
    """Resting size on each side of the book - where the money is sitting, as
    opposed to where it has already gone.

    Kalshi answers with `orderbook_fp`: price/size ladders as decimal strings,
    one for yes and one for no. `near` limits the second pair of numbers to the
    orders close enough to the touch to actually matter; size parked ten cents
    away is a wish, not a bid.
    """
    try:
        js = kalshi._get(f"/markets/{ticker}/orderbook", {"depth": 20})
    except Exception as e:
        print(f"[flow] {ticker}: no book ({type(e).__name__}: {str(e)[:60]})")
        return None
    ob = js.get("orderbook_fp") or js.get("orderbook") or {}
    sides = {}
    for key, name in (("yes_dollars", "yes"), ("no_dollars", "no"),
                      ("yes", "yes"), ("no", "no")):
        ladder = ob.get(key)
        if not ladder or name in sides:
            continue
        rows = []
        for entry in ladder:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            px, sz = kalshi._f(entry[0]), kalshi._f(entry[1])
            if px is None or sz is None or sz <= 0:
                continue
            rows.append((px if px <= 1 else px / 100.0, sz))
        if rows:
            sides[name] = rows
    if "yes" not in sides or "no" not in sides:
        return None
    y, n = sides["yes"], sides["no"]
    top_y, top_n = max(p for p, _ in y), max(p for p, _ in n)
    ny = sum(sz for p, sz in y if p >= top_y - near)
    nn = sum(sz for p, sz in n if p >= top_n - near)
    ty, tn = sum(sz for _, sz in y), sum(sz for _, sz in n)
    return {"yes_size": round(ty, 1), "no_size": round(tn, 1),
            "yes_share": round(ty / (ty + tn), 4) if ty + tn else None,
            "near_yes": round(ny, 1), "near_no": round(nn, 1),
            "near_yes_share": round(ny / (ny + nn), 4) if ny + nn else None,
            "best_yes": round(top_y, 4), "best_no": round(top_n, 4)}


def read(series, ticker, hours=LOOKBACK_H, now=None, limit=500, with_book=True):
    """Everything flow knows about one side of one game."""
    h = history(series, ticker, hours=hours, now=now)
    return {"move": move(h), "tape": taker_flow(ticker, limit=limit),
            "book": book(ticker) if with_book else None, "candles": len(h)}


def verdict(f, steam=STEAM):
    """Whether the market has been moving toward this side or away from it.

    'toward' is not automatically good news for a bet on it. It means the price
    we are getting is worse than it was and the market has already agreed with
    us; 'away' means we are either early or wrong. Which of those it is, is what
    the record has to settle - so this labels, and does not judge.
    """
    m = (f or {}).get("move")
    if not m:
        return ""
    d = m["delta"]
    if d >= steam:
        return "toward"
    if d <= -steam:
        return "away"
    return "flat"


def note(f, side_name, steam=STEAM):
    """One line for the card."""
    if not f:
        return ""
    bits = []
    m = f.get("move")
    if m:
        v = verdict(f, steam)
        word = {"toward": "steamed to", "away": "drifted to", "flat": "sat at"}[v]
        bits.append(f"{side_name} {word} {100 * m['now']:.0f}c from "
                    f"{100 * m['open']:.0f}c over {m['hours']:.0f}h")
    t = f.get("tape")
    if t:
        bits.append(f"tape {100 * t['ticket_share']:.0f}% of {t['trades']} trades / "
                    f"{100 * t['money_share']:.0f}% of {t['contracts']:.0f} contracts"
                    + (f", biggest {t['max_size']:.0f}" if t.get("max_size") else "")
                    + (f", {t['blocks']} block" if t.get("blocks") else ""))
    b = f.get("book")
    if b and b.get("near_yes_share") is not None:
        bits.append(f"book {100 * b['near_yes_share']:.0f}% of the size near the touch")
    return " | ".join(bits)


def disagrees(f, min_gap=0.15):
    """True when the prints and the size are on opposite sides of the market.

    This is the shape a sportsbook split is pointing at: most of the tickets on
    one side, most of the money on the other. On an exchange it is rarer and
    noisier, because there is no vig to draw square money in.
    """
    t = (f or {}).get("tape")
    if not t:
        return False
    return abs(t["gap"]) >= min_gap and (t["ticket_share"] - 0.5) * (t["money_share"] - 0.5) < 0


# ---------------------------------------------------------------------- CLI
def check(league_ticker, n=2, hours=LOOKBACK_H):
    """Read flow off the most-traded open markets in a series, and print it.

    This exists because the first live version of taker_flow returned None on
    every pick and the pick log could not say why: a pick is written once, so a
    re-run with a fix does not refresh it. Ask the market directly instead.
    """
    evs = [e for e in kalshi.open_events(league_ticker, 0.15, "away_home") if e["sides"]]
    if not evs:
        print(f"[flow] {league_ticker}: nothing open")
        return 1
    evs.sort(key=lambda e: -max((s.get("vol") or 0) for s in e["sides"]))
    ok = 0
    for ev in evs[:n]:
        side = max(ev["sides"], key=lambda s: s.get("vol") or 0)
        f = read(league_ticker, side["ticker"], hours=hours)
        print(f"\n[flow] {side['ticker']}  vol {side.get('vol'):.0f}  ask {side.get('ask')}")
        print(f"[flow]   history : {f['candles']} candles -> {f['move']}")
        print(f"[flow]   tape    : {f['tape']}")
        print(f"[flow]   book    : {f['book']}")
        print(f"[flow]   reads as: {note(f, side['name']) or '(nothing)'}")
        if f["tape"] and f["book"]:
            ok += 1
    print(f"\n[flow] {ok}/{min(n, len(evs))} markets gave a full reading")
    return 0 if ok else 1


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="What the market did before we got here")
    ap.add_argument("--check", default="KXNCAAFGAME",
                    help="series ticker to read a live sample from")
    ap.add_argument("--n", type=int, default=2, help="how many markets to sample")
    ap.add_argument("--hours", type=int, default=LOOKBACK_H)
    a = ap.parse_args(argv)
    return check(a.check, a.n, a.hours)


if __name__ == "__main__":
    raise SystemExit(_cli())
