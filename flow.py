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


def taker_flow(ticker, limit=500, big=BIG_TRADE):
    """Tickets versus money, from the tape.

    `count` on a Kalshi trade is contracts, and `taker_side` is who crossed the
    spread to get filled - the aggressor, which is the side with an opinion. A
    resting order that gets hit is not making the argument; the taker is.
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
    sizes, big_yes, big_no = [], 0, 0
    for t in trades:
        cnt = kalshi._f(t.get("count")) or 0.0
        side = (t.get("taker_side") or "").lower()
        if side not in ("yes", "no") or cnt <= 0:
            continue
        sizes.append(cnt)
        if side == "yes":
            n_yes += 1
            c_yes += cnt
            big_yes += cnt if cnt >= big else 0
        else:
            n_no += 1
            c_no += cnt
            big_no += cnt if cnt >= big else 0
    n, c = n_yes + n_no, c_yes + c_no
    if not n or not c:
        return None
    sizes.sort()
    return {
        "trades": n, "contracts": round(c, 1),
        "ticket_share": round(n_yes / n, 4),        # share of PRINTS buying this side
        "money_share": round(c_yes / c, 4),         # share of CONTRACTS buying it
        "gap": round(c_yes / c - n_yes / n, 4),     # + = the big money agrees with the crowd
        "median_size": sizes[len(sizes) // 2],
        "max_size": sizes[-1],
        "big_share": round((big_yes + big_no) / c, 4),
        "big_money_share": round(big_yes / (big_yes + big_no), 4) if (big_yes + big_no) else None,
    }


def read(series, ticker, hours=LOOKBACK_H, now=None, limit=500):
    """Everything flow knows about one side of one game."""
    h = history(series, ticker, hours=hours, now=now)
    return {"move": move(h), "tape": taker_flow(ticker, limit=limit),
            "candles": len(h)}


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
                    + (f", biggest {t['max_size']:.0f}" if t.get("max_size") else ""))
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
