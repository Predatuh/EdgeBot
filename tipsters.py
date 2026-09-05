"""Track an outside tipster's slate against the same Kalshi market EdgeBot bets.

Paste a slate (e.g. a Discord post) and this matches each line to a live Kalshi
event, records the price you could have taken at that moment, what EdgeBot's own
model thought, and whether the two agree. Picks then auto-grade from Kalshi
settlements exactly like EdgeBot's own, so you get a like-for-like record.

  python tipsters.py --name fph0 --slate-file slate.txt

Line format is forgiving: "Alex Michelsen vs Daniel Merida - Merida ML".
Headers, reactions and chatter are ignored. Spread / parlay / prop lines are
logged with their bet_type and excluded from the moneyline ROI.
"""
import argparse
import datetime as dt
import difflib
import os
import re
import sys
import unicodedata

import yaml

import kalshi, state

HERE = os.path.dirname(os.path.abspath(__file__))
MATCH_MIN = 0.72                      # event-match confidence floor
SPLIT = re.compile(r"\s+(?:vs\.?|v\.?|@|versus)\s+", re.I)
SPREAD = re.compile(r"[+-]\d+(\.\d+)?\b")
ASIDE = re.compile(r"\([^)]*\)")                 # "(might sprinkle a little...)" is commentary
# a set/game market, or an in-play instruction — neither is a match moneyline
LIVE = re.compile(r"\bset\s*\d|→|->|\bswitch\b|\brest of the (match|game)\b", re.I)


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _sim(a, b):
    """0..1 similarity between two competitor names."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    wa, wb = na.split(), nb.split()
    shared = set(wa) & set(wb)
    if shared:                        # a shared surname / city is a strong signal
        return 0.80 + 0.20 * len(shared) / max(len(wa), len(wb))
    # initials: "Paris Saint Germain" == "PSG"
    for x, y in ((wa, nb), (wb, na)):
        if len(x) > 1 and "".join(w[0] for w in x) == y.replace(" ", ""):
            return 0.95
    return difflib.SequenceMatcher(None, na, nb).ratio()


def parse_slate(text):
    """Free text -> [{a, b, pick, bet_type, raw}]. bet_type: ML | ML+ | SPREAD | OTHER."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("*•-").strip()
        if not line or " - " not in line and " – " not in line:
            continue
        line = line.replace(" – ", " - ")
        left, _, right = line.partition(" - ")
        parts = SPLIT.split(left)
        if len(parts) != 2:
            continue
        a, b = parts[0].strip(), parts[1].strip()
        if not a or not b or len(a) > 60 or len(b) > 60:
            continue
        # drop parenthetical commentary first, so an aside like "(Bayern -1.5)"
        # cannot turn a plain moneyline into a spread
        pick_txt = ASIDE.sub(" ", right).strip()
        if not pick_txt:
            continue
        if LIVE.search(pick_txt):                     # set market / live switch: record, never score
            name = re.split(r"\bset\b|→|->|\bswitch\b", pick_txt, flags=re.I)[0]
            name = re.sub(r"\b(ml|moneyline|money line)\b", "", name, flags=re.I).strip(" .-")
            if name:
                out.append({"a": a, "b": b, "pick": name, "bet_type": "OTHER", "raw": line})
            continue
        extra = "+" in pick_txt                       # extra legs on the ticket
        head = pick_txt.split("+")[0].strip()
        spread = bool(SPREAD.search(head))
        name = re.sub(r"\b(ml|moneyline|money line)\b", "", head, flags=re.I)
        name = SPREAD.sub("", name).strip(" .-")
        if not name:
            continue
        bet = "SPREAD" if spread else ("ML+" if extra else
              ("ML" if re.search(r"\bml\b|moneyline", pick_txt, re.I) else "OTHER"))
        out.append({"a": a, "b": b, "pick": name, "bet_type": bet, "raw": line})
    return out


def match_event(tip, events):
    """Best (event, side, confidence) for one parsed line, or (None, None, 0)."""
    best, best_score = None, 0.0
    for ev in events:
        sides = [s for s in kalshi.match_sides(ev) if not s["is_tie"]]
        if len(sides) != 2:
            continue
        n0, n1 = sides[0]["name"], sides[1]["name"]
        score = max(_sim(tip["a"], n0) + _sim(tip["b"], n1),
                    _sim(tip["a"], n1) + _sim(tip["b"], n0)) / 2.0
        if score > best_score:
            best, best_score = (ev, sides), score
    if not best or best_score < MATCH_MIN:
        return None, None, best_score
    ev, sides = best
    side = max(sides, key=lambda s: _sim(tip["pick"], s["name"]))   # binary: argmax, no floor
    return ev, side, best_score


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


def _board(cfg, date, backfill):
    """Kalshi events around `date`: open markets, plus settled ones when backfilling.

    A slate's "today" does not line up with Kalshi's UTC event dating - a US evening
    match posted tonight can be dated either side of midnight - so accept a one-day
    window on each side rather than an exact date."""
    d = dt.date.fromisoformat(date)
    window = {(d + dt.timedelta(days=n)).isoformat() for n in (-1, 0, 1)}
    events, seen = [], set()
    since = state._ts(dt.date.fromisoformat(date) - dt.timedelta(days=1)) if backfill else None
    for key, lg in cfg["leagues"].items():
        if not lg.get("enabled", True):
            continue
        evs = []
        try:
            evs += kalshi.open_events(lg["ticker"], cfg.get("max_spread"))
        except Exception as e:
            print(f"[tipster] {key} open board failed: {type(e).__name__}: {str(e)[:70]}")
        if backfill:
            # a past-day slate can hold matches that have settled AND ones still running
            try:
                evs += kalshi.settled_events(lg["ticker"], since)
            except Exception as e:
                print(f"[tipster] {key} settled board failed: {type(e).__name__}: {str(e)[:70]}")
        for ev in evs:
            if ev["date"] in window and ev["event"] not in seen:
                seen.add(ev["event"])
                ev["_league"] = key
                events.append(ev)
    return events


def _price_from_own_log(eb, side_name):
    """A settled market only reports its closing price, so for a backfill take the
    entry price from our own log: the real ask when we picked the same side, or the
    de-vigged complement plus the same vig when we were on the other one."""
    if not eb or not eb.get("price"):
        return None, None, ""
    price, mkt = state._fl(eb["price"]), state._fl(eb["market_prob"])
    if eb.get("pick") == side_name:
        return price, mkt, "eb_log"
    if not mkt:
        return None, None, ""
    return round((1 - mkt) + (price - mkt), 4), round(1 - mkt, 4), "eb_log_derived"


def record(name, text, date=None, cfg=None, backfill=False):
    """Match a slate to the Kalshi board and log it. Returns (logged, skipped)."""
    cfg = cfg or load_config()
    date = date or dt.date.today().isoformat()
    tips = parse_slate(text)
    if not tips:
        print("[tipster] nothing parsed from that slate")
        return 0, 0
    print(f"[tipster] {name}: parsed {len(tips)} lines")

    events = _board(cfg, date, backfill)
    print(f"[tipster] {len(events)} {'settled' if backfill else 'live'} events on the board around {date}")

    own = {r["event_id"]: r for r in state.read_log()}
    logged = skipped = 0
    for t in tips:
        ev, side, conf = match_event(t, events)
        if not ev:
            print(f"[tipster] no match ({conf:.2f}): {t['raw'][:70]}")
            skipped += 1
            continue
        eb = own.get(ev["event"], {})
        gradeable = t["bet_type"] in ("ML", "ML+")
        if backfill:
            price, mprob, psrc = _price_from_own_log(eb, side["name"])
        else:
            price, mprob, psrc = side["ask"], side["prob"], "live"
        row = {
            "date": date, "tipster": name, "league": ev.get("_league", ""),
            "event_id": ev["event"], "matchup": t["a"] + " vs " + t["b"],
            "pick": side["name"], "bet_type": t["bet_type"], "raw": t["raw"],
            "price": price if (gradeable and price) else "",
            "market_prob": round(mprob, 3) if mprob is not None else "",
            "price_src": psrc,
            "eb_pick": eb.get("pick", ""), "eb_tier": eb.get("tier", ""),
            "eb_model_prob": eb.get("model_prob", ""),
            "agree": ("yes" if eb.get("pick") == side["name"] else "no") if eb else "",
            "match_conf": round(conf, 2),
            # non-moneyline tickets are recorded but never scored
            "result": "" if (gradeable and price) else "n/a",
            "close_prob": "", "clv": "", "profit_100": "",
        }
        if state.append_tip(row):
            logged += 1
            flag = "" if gradeable else f"  [{t['bet_type']}, not scored]"
            print(f"[tipster] {side['name']} @ {row['price'] or '-'} — {ev['event']}{flag}")
        else:
            skipped += 1
    linked = state.link_own_picks()
    if linked:
        print(f"[tipster] linked {linked} row(s) to our own picks")
    print(f"[tipster] logged {logged}, skipped {skipped}")
    return logged, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Log an outside tipster's slate")
    ap.add_argument("--name", required=True)
    ap.add_argument("--slate")
    ap.add_argument("--slate-file")
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true",
                    help="slate is from a past day: match settled markets and take entry "
                         "prices from our own pick log")
    a = ap.parse_args(argv)
    text = a.slate
    if a.slate_file:
        with open(a.slate_file) as f:
            text = f.read()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read()
    if not text:
        ap.error("give --slate, --slate-file or pipe the slate on stdin")
    cfg = load_config()
    kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
    record(a.name, text, a.date, cfg, a.backfill)
    return 0


if __name__ == "__main__":
    sys.exit(main())
