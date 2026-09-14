#!/usr/bin/env python3
"""Why the tape came back empty. Tries the endpoint several ways on a market we
know is liquid, and prints what actually comes back."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kalshi


def show(label, path, params):
    try:
        js = kalshi._get(path, params)
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")
        print(f"  {label}: {type(e).__name__} {str(e)[:90]} {body[:120]}")
        return None
    keys = sorted(js.keys())
    n = {k: (len(v) if isinstance(v, list) else v) for k, v in js.items() if k != "cursor"}
    print(f"  {label}: keys={keys} sizes={n}")
    for k, v in js.items():
        if isinstance(v, list) and v:
            print(f"    first {k}: {json.dumps(v[0])[:300]}")
    return js


evs = [e for e in kalshi.open_events("KXNFLGAME", 0.15, "away_home") if e["sides"]]
if not evs:
    evs = [e for e in kalshi.open_events("KXNCAAFGAME", 0.15, "away_home") if e["sides"]]
best = max(evs, key=lambda e: max((s.get("vol") or 0) for s in e["sides"]))
side = max(best["sides"], key=lambda s: s.get("vol") or 0)
tk = side["ticker"]
print(f"most-traded open market: {tk}  vol={side.get('vol')}  oi={side.get('oi')}\n")

show("/markets/trades ticker=", "/markets/trades", {"ticker": tk, "limit": 20})
show("/markets/trades market_ticker=", "/markets/trades", {"market_ticker": tk, "limit": 20})
show("/markets/{t}/trades", f"/markets/{tk}/trades", {"limit": 20})
show("/markets/trades no ticker", "/markets/trades", {"limit": 5})
show("/markets/{t}/orderbook", f"/markets/{tk}/orderbook", {"depth": 5})
