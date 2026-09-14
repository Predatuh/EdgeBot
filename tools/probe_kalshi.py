#!/usr/bin/env python3
"""What Kalshi actually exposes: order flow, price history, and market types.

Run from a GitHub runner - Kalshi is unreachable from the dev container, and it
403s anything carrying an Origin header, so this is the only place to ask.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kalshi


def probe_trades(ticker):
    print("\n=== GET /markets/trades")
    try:
        r = kalshi._get("/markets/trades", {"ticker": ticker, "limit": 50})
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:200])
        return
    ts = r.get("trades") or []
    print("  trades:", len(ts), "| keys:", sorted(ts[0].keys()) if ts else "none")
    for t in ts[:5]:
        print("   ", {k: t.get(k) for k in
                      ("created_time", "count", "yes_price", "no_price", "taker_side")})
    if ts:
        sizes = sorted(t.get("count") or 0 for t in ts)
        yes = sum(t.get("count") or 0 for t in ts if t.get("taker_side") == "yes")
        no = sum(t.get("count") or 0 for t in ts if t.get("taker_side") == "no")
        print(f"  contracts {sum(sizes)} | median size {sizes[len(sizes)//2]} "
              f"| max {sizes[-1]} | taker yes {yes} vs no {no}")
        print("  -> tickets-vs-money analogue is", "USABLE" if yes or no else "NOT USABLE")


def probe_candles(series, ticker):
    print("\n=== candles")
    now = int(time.time())
    for hours, period in ((24 * 7, 60), (24, 60)):
        try:
            cs = kalshi.candles(series, ticker, now - hours * 3600, now, period)
        except Exception as e:
            print(f"  {hours}h FAILED:", type(e).__name__, str(e)[:160])
            continue
        print(f"  {hours}h @{period}m: {len(cs)} candles"
              + (f" | keys {sorted(cs[0].keys())}" if cs else ""))
        if cs:
            print("   first:", json.dumps(cs[0])[:260])
            print("   last :", json.dumps(cs[-1])[:260])


def probe_series():
    print("\n=== football series Kalshi lists")
    try:
        r = kalshi._get("/series", {"category": "Sports", "limit": 200})
        ser = r.get("series") or []
        print("  series returned:", len(ser))
        hits = [s for s in ser if any(k in (s.get("ticker") or "").upper()
                                      for k in ("SPREAD", "TOTAL", "MARGIN", "HANDICAP"))
                and any(k in (s.get("ticker") or "").upper() for k in ("NFL", "NCAAF", "CFB"))]
        for s in hits:
            print("   ", s.get("ticker"), "|", (s.get("title") or "")[:70])
        if not hits:
            print("    none matched")
        return
    except Exception as e:
        print("  /series failed:", type(e).__name__, str(e)[:140], "- guessing instead")
    for guess in ("KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAMESPREAD", "KXNFLGAMETOTAL",
                  "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNFLPOINTS"):
        try:
            n = len(kalshi._get("/markets", {"series_ticker": guess, "limit": 1})
                    .get("markets") or [])
            print(f"   {guess}: {n} market(s)")
        except Exception as e:
            print(f"   {guess}: {type(e).__name__}")


def main():
    for series in ("KXNCAAFGAME", "KXNFLGAME"):
        evs = [e for e in kalshi.open_events(series, 0.15, "away_home") if e["sides"]]
        if not evs:
            print(f"=== {series}: nothing open")
            continue
        best = max(evs, key=lambda e: max((s.get("vol") or 0) for s in e["sides"]))
        side = max(best["sides"], key=lambda s: s.get("vol") or 0)
        print(f"\n######## {series}: {side['ticker']} "
              f"(vol {side.get('vol')}, oi {side.get('oi')}, ask {side.get('ask')})")
        probe_trades(side["ticker"])
        probe_candles(series, side["ticker"])
    probe_series()


if __name__ == "__main__":
    main()
