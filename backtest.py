"""Replay the parlay builder at a past moment and grade every ticket it would have made.

WHAT MAKES THIS HONEST
----------------------
The only prices used are Kalshi candlestick quotes from at or before the cutoff
timestamp. A settled market's last_price is the in-play settlement trade (~99c on
the winner), so a backtest priced off it would report every ticket as a lock. That
mistake already cost this repo 19 logged rows once; `settle_price` is read here for
exactly one purpose - who won - and never for a price.

A market with no quote before the cutoff is skipped, not back-filled. If it was not
listed on Friday morning you could not have bet it on Friday morning.

Usage:
    python backtest.py --at 2026-09-11T13:00Z --days 3
"""
import argparse
import datetime as dt
import json
import os

import kalshi
import parlay

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)


def board_at(cfg, cutoff, days, leagues=None, max_games=None):
    """The legs that were actually buyable at `cutoff`, plus who went on to win."""
    ts = int(cutoff.timestamp())
    horizon = {(cutoff.date() + dt.timedelta(days=n)).isoformat() for n in range(days + 1)}
    legs, winners, seen, skipped = [], {}, 0, 0

    for key in (leagues or parlay.all_leagues(cfg)):
        spec = parlay.league_spec(cfg, key)
        if not spec:
            continue
        try:
            evs = kalshi.settled_events(spec["ticker"], ts, spec["ticker_order"])
        except Exception as e:
            print(f"[backtest] {key}: {type(e).__name__}: {str(e)[:70]}")
            continue
        evs = [e for e in evs if e.get("date") in horizon]
        if max_games:
            evs = evs[:max_games]
        got = 0
        for ev in evs:
            sides = kalshi.match_sides(ev)
            if not sides or not all(s.get("settled") for s in sides):
                continue
            priced = []
            for s in sides:
                bid, ask, mid = kalshi.quote_at(spec["ticker"], s["ticker"], ts)
                if mid is None:
                    skipped += 1
                    continue
                priced.append(dict(s, prob=mid, ask=ask, bid=bid, vol=0, oi=0))
            if len(priced) != len(sides):
                continue                       # a half-quoted game is not a game you could bet
            seen += 1
            fake = dict(ev, sides=priced)
            new = parlay.legs_from_events([fake], key, spec["label"])
            for l in new:
                l["date"] = ev.get("date", "")
            legs += new
            got += len(new)
            for s in priced:
                winners[s["ticker"]] = bool(s["won"])
        print(f"[backtest] {key}: {len(evs)} settled games in window -> {got} legs")
    print(f"[backtest] {seen} fully quoted games, {skipped} sides had no quote at the cutoff")
    return legs, winners


def grade(ticket, winners):
    """Did it cash? And if not, which leg killed it."""
    lost = [l for l in ticket["legs"] if not winners.get(l["ticker"], False)]
    return {
        "won": not lost,
        "lost_n": len(lost),
        "killers": [{"pick": l["pick"], "opp": l["opp"], "p": l["p"],
                     "league": l["league_label"]} for l in sorted(lost, key=lambda x: -x["p"])[:3]],
    }


def configs():
    """Thirty tickets: every preset across every scope the board can fill."""
    out = []
    for sc in parlay.SCOPES:
        for ps in parlay.PRESETS:
            out.append({"scope": sc["key"], "scope_label": sc["label"], "scope_emoji": sc["emoji"],
                        "preset": ps["key"], "preset_label": ps["label"], "emoji": ps["emoji"],
                        "target": ps["target"], "min_leg": ps["min_leg"], "max_legs": ps["max_legs"]})
    return out


def run(cutoff, days=3, leagues=None, stake=10.0, max_games=None):
    cfg = parlay.load_config()
    kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
    legs, winners = board_at(cfg, cutoff, days, leagues, max_games)
    legs.sort(key=lambda l: -l["p"])

    tickets, seen = [], set()
    for c in configs():
        pool = parlay.scope_legs(legs, c["scope"])
        r = parlay.build(pool, target=c["target"], min_leg=c["min_leg"],
                         max_legs=c["max_legs"], exclude_flagged=False)
        if not r:
            continue
        sig = (c["scope"], tuple(sorted(l["ticker"] for l in r["legs"])))
        if sig in seen:
            continue                       # same bet reached by two presets
        seen.add(sig)
        r.update(c)
        r.update(grade(r, winners))
        r["returned"] = round(stake * r["multiple"], 2) if r["won"] else 0.0
        r["profit"] = round(r["returned"] - stake, 2)
        tickets.append(r)

    n = len(tickets)
    won = sum(1 for t in tickets if t["won"])
    exp = sum(t["win_prob"] for t in tickets)
    staked = stake * n
    ret = sum(t["returned"] for t in tickets)
    return {
        "cutoff_utc": cutoff.strftime("%Y-%m-%dT%H:%MZ"),
        "days": days, "stake": stake,
        "board": parlay.board_summary(legs),
        "tickets": tickets,
        "summary": {
            "n": n, "won": won, "expected_won": round(exp, 2),
            "staked": round(staked, 2), "returned": round(ret, 2),
            "profit": round(ret - staked, 2),
            "roi": round((ret - staked) / staked, 4) if staked else 0.0,
            "modelled_return": round(sum(t["win_prob"] * stake * t["multiple"] for t in tickets), 2),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay the parlay builder at a past moment")
    ap.add_argument("--at", required=True, help="cutoff, e.g. 2026-09-11T13:00Z")
    ap.add_argument("--days", type=int, default=3, help="grade games starting within this many days")
    ap.add_argument("--leagues", default="", help="comma-separated subset")
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--max-games", type=int, default=0, help="cap per league (keeps API calls sane)")
    ap.add_argument("--json", default="", help="write the full result here")
    a = ap.parse_args(argv)

    res = run(parse_ts(a.at), a.days,
              [s.strip() for s in a.leagues.split(",") if s.strip()] or None,
              a.stake, a.max_games or None)

    s, b = res["summary"], res["board"]
    print(f"\nBoard at {res['cutoff_utc']}: {b['games']} games, {b['legs']} legs\n")
    for t in res["tickets"]:
        mark = "WON " if t["won"] else "lost"
        print(f"{mark} {t['scope_emoji']} {t['scope_label']:<11} {t['preset_label']:<9} "
              f"{t['n']:>2} legs  win {t['win_prob']*100:5.1f}%  pays {t['multiple']:7.2f}x  "
              f"P/L {t['profit']:+8.2f}" +
              ("" if t["won"] else f"   killed by {t['killers'][0]['pick']} "
                                   f"({t['killers'][0]['p']*100:.0f}c)"
                                   + (f" +{t['lost_n']-1} more" if t["lost_n"] > 1 else "")))
    print(f"\n{s['won']}/{s['n']} tickets cashed (the prices implied {s['expected_won']})")
    print(f"staked ${s['staked']:.2f}  returned ${s['returned']:.2f}  "
          f"P/L ${s['profit']:+.2f}  ROI {s['roi']*100:+.1f}%")
    print(f"what the prices said to expect: ${s['modelled_return']:.2f} back")

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as f:
            json.dump(res, f, separators=(",", ":"))
        print(f"[backtest] wrote {a.json}")
    return res


if __name__ == "__main__":
    main()
