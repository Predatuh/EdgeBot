"""One-off: dump what Kalshi's candlestick endpoint actually returns.

The backtest priced 0 of 665 sides, without raising, which means the request
succeeded and the parsing is wrong. Guessing again is cheaper than reading docs
only if the guess is informed by the real payload - so print it.
"""
import datetime as dt
import json

import kalshi

CUT = dt.datetime(2026, 9, 11, 13, 0, tzinfo=dt.timezone.utc)
ts = int(CUT.timestamp())

evs = kalshi.settled_events("KXNCAAFGAME", ts, "away_home")
print(f"{len(evs)} settled NCAAF events after the cutoff")
ev = evs[0]
print("event:", ev["event"], "date:", ev["date"], "close:", ev["close"])
side = ev["sides"][0]
print("side:", {k: side[k] for k in ("name", "ticker", "won", "settled")})
tk = side["ticker"]

for label, path, params in [
    ("series/markets/candlesticks 60m",
     f"/series/KXNCAAFGAME/markets/{tk}/candlesticks",
     {"start_ts": ts - 36 * 3600, "end_ts": ts, "period_interval": 60}),
    ("series/markets/candlesticks 1440m",
     f"/series/KXNCAAFGAME/markets/{tk}/candlesticks",
     {"start_ts": ts - 7 * 86400, "end_ts": ts, "period_interval": 1440}),
    ("markets/candlesticks (no series)",
     f"/markets/{tk}/candlesticks",
     {"start_ts": ts - 36 * 3600, "end_ts": ts, "period_interval": 60}),
    ("market_history",
     "/markets/trades",
     {"ticker": tk, "limit": 5}),
]:
    print("\n=== " + label)
    try:
        js = kalshi._get(path, params)
    except Exception as e:
        print("   ERROR", type(e).__name__, str(e)[:200])
        continue
    if not isinstance(js, dict):
        print("   non-dict:", str(js)[:200]); continue
    print("   top-level keys:", list(js.keys()))
    for k, v in js.items():
        if isinstance(v, list):
            print(f"   {k}: {len(v)} items")
            if v:
                print("   first item:")
                print(json.dumps(v[0], indent=4)[:1200])
                if len(v) > 1:
                    print("   last item:")
                    print(json.dumps(v[-1], indent=4)[:1200])
            break
