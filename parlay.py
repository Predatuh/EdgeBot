"""Football-only parlay builder for Kalshi combos.

THE MATH THIS IS BUILT AROUND
-----------------------------
Kalshi prices a combo as the product of its legs. So for a fairly priced parlay:

    win probability  =  1 / payout multiple

A 3-leg combo paying 11x and a 39-leg combo paying 11x are the SAME 9% bet. The leg
count is not the risk - the payout is. And because Kalshi quotes a spread in CENTS,
reaching a given payout with more legs takes another bite each time:

    EV = product(mid_i / ask_i) - 1        (always negative, growing with leg count)

Two consequences drive every choice below:
  1. For a target payout, use the FEWEST legs that get you there.
  2. Cheap legs are not the enemy; extra legs are. Reaching 5x out of 97c favourites
     takes 27 of them and costs -21.5% to spread. Eight mid-priced legs reach the
     same 5x at the same win probability for about -0%.

So the builder solves: given a payout you want, find the highest win probability
that still pays it. There is no price floor - a 55c game earns its place whenever
it buys payout more cheaply than another 97c leg would.
"""
import datetime as dt
import json
import math
import os

import kalshi

HERE = os.path.dirname(os.path.abspath(__file__))

TIERS = [                       # (min de-vigged probability, key, label, emoji)
    (0.97, "lock",   "Lock",        "🔒"),
    (0.92, "strong", "Strong",      "💪"),
    (0.85, "solid",  "Solid",       "✅"),
    (0.70, "lean",   "Lean",        "⚖️"),
    (0.00, "live",   "Coin flip",   "🎲"),
]

# Presets target a PAYOUT, because that is the thing you actually choose. Win
# probability is then whatever the board's best route to that payout gives you.
#
# The earlier ladder targeted a win probability behind a hard price floor, and that
# was backwards. Kalshi's spread is about 1c whatever the contract costs, so a cent
# on a 97c leg is 1% of its value and a cent on a 50c leg is 2% - but reaching a
# given payout out of 97c legs takes MANY more of them, and you cross a spread every
# time. On a real board, 5x costs 27 chalk legs at -21.5% EV, or 8 mid-priced legs at
# about -0%. Same win probability, same payout. Above 5x the chalk route does not
# exist at all, which is why a "Lottery" rung used to hand back 1.3x.
#
# So there is no price floor. The builder takes whichever legs buy the payout most
# cheaply in win probability, and a 55c game earns its place when it is the efficient
# route. What still protects you is liquidity, not price.
# Win probability is about 1/payout however you build it - that identity is not
# negotiable, so these rungs are really one continuous dial from "nearly certain,
# pays little" to "almost never, pays a fortune".
PRESETS = [
    {"key": "banker",  "label": "Banker",  "payout": 1.15,  "emoji": "🔒"},
    {"key": "solid",   "label": "Solid",   "payout": 2.0,   "emoji": "🛡️"},
    {"key": "swing",   "label": "Swing",   "payout": 8.0,   "emoji": "⚖️"},
    {"key": "longshot","label": "Longshot","payout": 40.0,  "emoji": "🎯"},
    {"key": "lottery", "label": "Lottery", "payout": 250.0, "emoji": "🎰"},
]

# What our own 422 graded picks did against their price, by bucket. Favourite-longshot
# bias is one of the most replicated findings in betting markets and our sample points
# the same way, so it is applied when CHOOSING legs - a cheap leg has to be enough
# cheaper to be worth it. It is never applied to the win probability shown, which
# stays the market's own number. `trust_cheap=True` turns the penalty off.
#
# These are the starting numbers, not the last word: paper.py measures what each band
# actually does and writes the result to the calibration file below. Once a band has
# enough settled legs to mean anything, its measured number replaces the one here -
# which is the only way this thing ever learns from being wrong.
HAND_DISCOUNT = [(0.90, 1.000, "90c+"), (0.60, 0.990, "60-90c"),
                 (0.40, 0.966, "40-60c"), (0.00, 0.936, "under 40c")]
PAPER_CALIBRATION = os.path.join(HERE, "data", "v2", "paper", "calibration.json")
_LEARNED = None


def learned_discounts(path=None, reload=False):
    """The measured per-band factors, or {} before there is enough evidence.

    Read straight from the file rather than importing paper.py: the dependency
    runs the other way (paper builds tickets with this module's ladder), and a
    cycle between them would be a real one.
    """
    global _LEARNED
    if _LEARNED is None or reload or path:
        try:
            with open(path or PAPER_CALIBRATION, encoding="utf-8") as f:
                got = json.load(f).get("learned_discount") or {}
        except (OSError, ValueError):
            got = {}
        if path and not reload:
            return got                      # a one-off read does not poison the cache
        _LEARNED = got
    return _LEARNED


def discount(p):
    learned = learned_discounts()
    for lo, hand, band in HAND_DISCOUNT:
        if p >= lo:
            return learned.get(band, hand)
    return HAND_DISCOUNT[-1][1]


def discount_table():
    """What the builder is actually using, so the app can build the same ticket.

    The page carries its own copy of the builder; if the bot started discounting
    a band differently and the phone did not, the two would quietly disagree
    about which legs are worth buying.
    """
    learned = learned_discounts()
    return [{"min": lo, "band": band, "factor": learned.get(band, hand),
             "learned": band in learned} for lo, hand, band in HAND_DISCOUNT]

MAX_LEGS = 40           # a ticket longer than this is unmanageable to actually place
LIQUID_SPREAD = 0.03    # a wider book than this is not really tradeable at the shown price
LIQUID_VOL = 50         # contracts traded; below this the price is barely a price

# Observed calibration of the Kalshi price, from our own graded picks. Reported to the
# user as context only - it is NOT applied to any probability. n is small and these are
# picks our bot chose, not a random sample, so treating it as an edge would be exactly
# the overfitting that produced the 3-24 staking record.
CALIBRATION_NOTE = (
    "Kalshi price vs what actually happened, 422 graded picks: >=95c went +2.9% per leg, "
    "90-95c +4.5%, 40-60c -3.4%, under 40c -6.4%. Favourites hold up; longshots do not. "
    "Small sample and self-selected, so it is context, not an edge."
)


def tier_of(p):
    for lo, key, label, emoji in TIERS:
        if p >= lo:
            return key, label, emoji
    return "live", "Coin flip", "🎲"


def _devig(a, b):
    """Two-way de-vig: normalise the pair to sum to 1."""
    s = (a or 0) + (b or 0)
    return (a / s, b / s) if s > 0 else (None, None)


def _normalise(sides):
    """Every priced outcome, scaled to sum to 1.

    This is the whole reason non-football sports needed care. Soccer and Test
    cricket price three outcomes, and a DRAW LOSES a win contract. De-vigging
    just the two teams would report P(win | no draw) - for a tight league match
    that reads 55c when the contract is really worth 40c. Dividing by the full
    book, draw included, is what makes a soccer leg comparable to an NFL leg.
    """
    priced = [s for s in sides if s.get("prob") is not None]
    total = sum(s["prob"] for s in priced)
    if len(priced) < 2 or total <= 0:
        return None, False
    return {id(s): s["prob"] / total for s in priced}, any(s["is_tie"] for s in priced)


def legs_from_events(evs, league, label):
    """One candidate leg per side that can actually win the market.

    Two-way sports give two legs a game. Three-way sports give two as well - the
    draw is priced into both, but is never itself a leg, because nobody builds a
    parlay out of draws and the tier language ("Lock") would be a lie on one.
    """
    out = []
    for ev in evs:
        sides = kalshi.match_sides(ev)
        probs, has_draw = _normalise(sides)
        if probs is None:
            continue
        picks = [s for s in sides if not s["is_tie"] and s.get("prob") is not None]
        if len(picks) != 2:
            continue
        a, b = picks
        for side, opp in ((a, b), (b, a)):
            p = probs[id(side)]
            ask, bid = side["ask"], side.get("bid")
            if not ask or not (0 < ask < 1):
                continue
            spread = (ask - bid) if bid is not None else None
            # what this leg costs you in EV terms: you pay `ask` for something worth `p`
            drag = (p / ask) if ask else 0.0
            key, tlabel, emoji = tier_of(p)
            out.append({
                "league": league, "league_label": label,
                "event_id": ev["event"], "ticker": side["ticker"],
                "game": f"{a['name']} vs {b['name']}",
                "pick": side["name"], "opp": opp["name"],
                "draw": has_draw,                 # a tie loses this leg
                "home": bool(side.get("home")),
                "p": round(p, 4),                 # de-vigged true probability
                "ask": round(ask, 4),             # what you pay
                "bid": round(bid, 4) if bid is not None else None,
                "spread": round(spread, 4) if spread is not None else None,
                "drag": round(drag, 4),           # p/ask: 1.0 = free, lower = worse
                "vol": side.get("vol") or 0,
                "oi": side.get("oi") or 0,
                "close": ev.get("close", ""),
                "date": ev.get("date", ""),
                "tier": key, "tier_label": tlabel, "emoji": emoji,
                "flags": [], "notes": "", "research": None,
            })
    return out


def build(legs, target=0.5, min_leg=0.90, max_legs=12, exclude_flagged=True,
          one_per_game=True):
    """Pick the cheapest set of legs whose combined probability clears `target`.

    Greedy on cost-efficiency: each leg contributes -log(p) toward the probability
    budget and -log(p/ask) of unavoidable spread loss. Taking the legs with the
    lowest loss-per-unit-of-budget spends the budget as cheaply as possible, which
    is the right objective because payout is fixed by the probability you end at.
    """
    pool = [l for l in legs if l["p"] >= min_leg and l["ask"] < 0.999]
    if exclude_flagged:
        pool = [l for l in pool if not l["flags"]]
    if not pool:
        return None

    def cost_ratio(l):
        budget = -math.log(max(l["p"], 1e-9))          # probability this leg spends
        loss = -math.log(max(l["p"] / l["ask"], 1e-9))  # spread it costs
        return loss / budget if budget > 1e-9 else float("inf")

    # cheapest spread per unit of probability spent, then highest probability
    pool = sorted(pool, key=lambda l: (cost_ratio(l), -l["p"]))
    chosen, used_games, prob = [], set(), 1.0
    for l in pool:
        if len(chosen) >= max_legs:
            break
        if one_per_game and l["event_id"] in used_games:
            continue
        if prob * l["p"] < target:
            continue                   # this leg would drop us through the target
        chosen.append(l)
        used_games.add(l["event_id"])
        prob *= l["p"]
    if not chosen:
        return None
    return summarise(chosen)


def summarise(chosen):
    """True win probability, what Kalshi pays, and the EV of the ticket.

    EV uses min(p, ask) per leg. On a zero-vig book the de-vigged mid can land a
    hair above the ask through sub-cent rounding, and a tool that advertises
    positive edge off a rounding artefact is worse than one that shows none.
    """
    win = 1.0
    cost = 1.0                     # cost of $1 payout = product of asks
    fair = 1.0                     # same, but never crediting a leg with edge
    for l in chosen:
        win *= l["p"]
        cost *= l["ask"]
        fair *= min(l["p"], l["ask"])
    mult = (1.0 / cost) if cost > 0 else 0.0
    ev = (fair * mult - 1.0) if cost > 0 else -1.0     # per $1 staked, never positive
    return {
        "legs": chosen,
        "n": len(chosen),
        "win_prob": round(win, 6),
        "multiple": round(mult, 2),
        "breakeven": round(cost, 6),          # win probability needed to break even
        "ev": round(ev, 4),                   # negative = the spread you are paying
        "spread_drag": round(win / cost - 1 - (win * mult - 1), 6) if cost else 0,
        "one_in": round(1 / win) if win > 0 else None,
        "payout_per_10": round(10 * mult, 2),
        "weakest_leg": round(min(l["p"] for l in chosen), 4),
        "flags": sorted({f for l in chosen for f in l["flags"]}),
    }


def is_liquid(l, spread=LIQUID_SPREAD, vol=LIQUID_VOL):
    """Can you actually get filled near the shown price?

    This replaced the two price sliders. Price never told you whether a market was
    real; the book does. A 1c spread on a heavily traded game is tradeable at any
    price, and a 9c spread on nothing is not tradeable at 97c.
    """
    if l.get("spread") is not None and l["spread"] > spread:
        return False
    # volume is cumulative on a live market but per-period on a historical candle,
    # so open interest stands in for it when replaying the past
    return max(l.get("vol") or 0, l.get("oi") or 0) >= vol


def build_payout(legs, payout=5.0, max_legs=MAX_LEGS, exclude_flagged=True,
                 one_per_game=True, liquid_only=True, min_leg=0.0, trust_cheap=False):
    """The best shot at `payout`: the highest win probability that still pays it.

    You need the asks to multiply down to 1/payout, so each leg contributes
    -log(ask) toward that and costs -log(p) of your win probability. Taking the
    legs with the least probability cost per unit of payout is the right objective,
    and it is what lets a 55c game beat nine 97c favourites.

    The catch is overshoot. Ranking purely on that ratio, a 1.04x target grabs a
    50c leg - which pays 1.98x and wins 50%, when one 95c favourite pays 1.05x and
    wins 95%. So a leg that would blow past what is left to buy is only taken when
    nothing smaller will do, and then the cheapest one in win probability wins.
    """
    pool = [l for l in legs if l["p"] >= min_leg and 0 < l["ask"] < 0.999]
    if exclude_flagged:
        pool = [l for l in pool if not l["flags"]]
    if liquid_only:
        pool = [l for l in pool if is_liquid(l)]
    if not pool:
        return None

    def est(l):
        """What this leg is worth, never crediting it with edge, and docking a cheap
        one by how much its price band has historically underperformed."""
        v = min(l["p"], l["ask"])
        return v if trust_cheap else v * discount(l["p"])

    def gain(l):
        return -math.log(l["ask"])              # payout this leg buys

    def ratio(l):
        g = gain(l)
        return (-math.log(max(est(l), 1e-9)) / g) if g > 1e-12 else float("inf")

    need = math.log(max(payout, 1.0000001))
    got, chosen, used, left = 0.0, [], set(), sorted(pool, key=lambda l: (ratio(l), -l["p"]))
    while got < need - 1e-12 and len(chosen) < max_legs:
        avail = [l for l in left if not (one_per_game and l["event_id"] in used)]
        if not avail:
            break
        rem = need - got
        fits = [l for l in avail if gain(l) <= rem + 1e-12]
        # a leg that fits keeps the budget alive for cheaper ones after it; when
        # none fits, any of them finishes the job, so take the one that costs the
        # least win probability rather than the one with the prettiest ratio
        pick = min(fits, key=ratio) if fits else max(avail, key=est)
        chosen.append(pick)
        used.add(pick["event_id"])
        got += gain(pick)
        left = [l for l in left if l is not pick]
    if not chosen:
        return None
    r = summarise(chosen)
    r["requested"] = payout
    # Below this the rung is not the rung. A board that cannot reach 250x should say
    # so, not hand back 1.3x with the word "Lottery" on it.
    r["reached"] = r["multiple"] >= payout * 0.85
    return r


def ladder(legs, presets=None, **kw):
    """One parlay per preset, skipping any the board cannot support."""
    out = []
    for ps in (presets or PRESETS):
        r = build_payout(legs, payout=ps["payout"],
                         max_legs=ps.get("max_legs", MAX_LEGS), **kw)
        if r:
            r.update({k: ps[k] for k in ("key", "label", "emoji")})
            r["payout"] = ps["payout"]
            out.append(r)
    # two presets can land on the same ticket when the board is thin; keep the first
    seen, uniq = set(), []
    for r in out:
        sig = tuple(sorted(l["ticker"] for l in r["legs"]))
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(r)
    return uniq


def board_summary(legs):
    by_tier, by_league = {}, {}
    for l in legs:
        by_tier[l["tier"]] = by_tier.get(l["tier"], 0) + 1
        by_league[l["league_label"]] = by_league.get(l["league_label"], 0) + 1
    return {"legs": len(legs), "games": len({l["event_id"] for l in legs}),
            "draws": sum(1 for l in legs if l.get("draw")),
            "by_tier": by_tier, "by_league": by_league}


def scope_of(key):
    for sc in SCOPES:
        if sc["key"] == key:
            return sc
    return SCOPES[-1]


def scope_legs(legs, key):
    sc = scope_of(key)
    if not sc["leagues"]:
        return list(legs)
    keep = set(sc["leagues"])
    return [l for l in legs if l["league"] in keep]


def live_scopes(legs):
    """Only the scopes the board can actually fill, so the picker never offers a
    tab that opens on nothing. 'Everything' is kept whenever anything is on."""
    have = {l["league"] for l in legs}
    out = []
    for sc in SCOPES:
        n = len(legs) if not sc["leagues"] else sum(1 for l in legs if l["league"] in sc["leagues"])
        if n and (sc["leagues"] is None or set(sc["leagues"]) & have):
            out.append(dict(sc, legs=n, games=len({l["event_id"] for l in scope_legs(legs, sc["key"])})))
    return out


# ---------------------------------------------------------------- data + output
# The parlay board keeps its own league table rather than reading config.yaml's, so
# adding a football league here cannot change what the main bot rates and stakes.
# Anything config.yaml does define (ticker, label, ticker_order) wins, so the two
# never drift apart for NFL and NCAAF.
FOOTBALL = {
    "nfl":   {"ticker": "KXNFLGAME",   "label": "NFL",              "ticker_order": "away_home"},
    "ncaaf": {"ticker": "KXNCAAFGAME", "label": "College Football", "ticker_order": "away_home"},
    # CFL runs Jun-Nov and overlaps the NCAAF season. This series ticker is unconfirmed
    # (Kalshi is unreachable from the dev container); if it is wrong the league simply
    # contributes 0 legs and the run log says so.
    "cfl":   {"ticker": "KXCFLGAME",   "label": "CFL",              "ticker_order": "away_home"},
}


def load_config():
    import yaml
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


def league_spec(cfg, key):
    """config.yaml's entry for `key`, or parlay's own football table, or both."""
    spec = dict(FOOTBALL.get(key) or {})
    lg = (cfg.get("leagues") or {}).get(key) or {}
    for f in ("ticker", "label", "ticker_order"):
        if lg.get(f):
            spec[f] = lg[f]
    if not spec.get("ticker"):
        return None
    spec.setdefault("label", key.upper())
    spec.setdefault("ticker_order", "away_home")
    return spec


# Scopes are what the picker offers as one tap. They are pure league filters over a
# single fetch, so switching between them on the phone costs nothing and works offline.
SCOPES = [
    {"key": "football", "label": "Football",  "emoji": "🏈", "leagues": ["nfl", "ncaaf", "cfl"]},
    {"key": "nfl",      "label": "NFL",       "emoji": "🏆", "leagues": ["nfl"]},
    {"key": "ncaaf",    "label": "College",   "emoji": "🎓", "leagues": ["ncaaf"]},
    {"key": "soccer",   "label": "Soccer",    "emoji": "⚽", "leagues": ["epl", "laliga", "ligue1",
                                                                        "seriea", "bundesliga",
                                                                        "mls", "ucl"]},
    {"key": "tennis",   "label": "Tennis",    "emoji": "🎾", "leagues": ["atp", "wta", "challenger"]},
    {"key": "mlb",      "label": "Baseball",  "emoji": "⚾", "leagues": ["mlb"]},
    {"key": "cricket",  "label": "Cricket",   "emoji": "🏏", "leagues": ["cricket_t20i", "cricket_odi",
                                                                        "cricket_test", "cpl"]},
    {"key": "all",      "label": "Everything", "emoji": "🌐", "leagues": None},   # None = no filter
]


def all_leagues(cfg):
    """Every league worth pulling a board from: config.yaml's, plus parlay's own
    football table for anything config does not carry (CFL).

    `enabled: false` in config is about what the bot RATES, not what a market
    exists for - and this tool never uses a rating - so a disabled league is still
    offered here. `stake: false` likewise: it is a warning about our model, and
    there is no model in a parlay.
    """
    keys = list((cfg.get("leagues") or {}).keys())
    keys += [k for k in FOOTBALL if k not in keys]
    return keys


def fetch(cfg, days=8, leagues=None):
    """Every leg on the board over the next `days` days.

    NCAAF is a weekly sport, so a same-day-only view would be empty most of the week;
    the window is what makes this usable on a Tuesday.
    """
    today = dt.date.today()
    horizon = {(today + dt.timedelta(days=n)).isoformat() for n in range(days)}
    legs = []
    for key in (leagues or all_leagues(cfg)):
        spec = league_spec(cfg, key)
        if not spec:
            continue
        try:
            evs = kalshi.open_events(spec["ticker"], cfg.get("max_spread", 0.15),
                                     spec["ticker_order"])
        except Exception as e:
            print(f"[parlay] {key} board failed: {type(e).__name__}: {str(e)[:80]}")
            continue
        evs = [e for e in evs if e.get("date") in horizon]
        got = legs_from_events(evs, key, spec["label"])
        print(f"[parlay] {key}: {len(evs)} games in the next {days}d -> {len(got)} legs")
        legs += got
    return legs


def candidates(legs, cap=60):
    """The legs a ticket would actually use, across every scope and rung.

    Research used to run down the board by price, which now researches the wrong
    legs entirely: a 60c game can be in four tickets while a 96c blowout is in none.
    Asking the builder which legs it wants, then researching those, spends the same
    budget on the games you might really bet.
    """
    want, order = set(), []
    for sc in SCOPES:
        pool = scope_legs(legs, sc["key"])
        if not pool:
            continue
        for t in ladder(pool, exclude_flagged=False):
            for l in t["legs"]:
                if l["ticker"] not in want:
                    want.add(l["ticker"])
                    order.append(l)
    # top-up with the safest legs on the board, so a pinned favourite is covered too
    for l in sorted(legs, key=lambda x: -x["p"]):
        if len(order) >= cap:
            break
        if l["ticker"] not in want:
            want.add(l["ticker"])
            order.append(l)
    return order[:cap]


def add_research(legs, cfg, cap=60):
    """Attach what the news says about each candidate leg.

    Two separate things come back. A `flag` is an injury we can pin on THIS team and
    is strong enough to act on. Everything else is a `note` - shown, never acted on,
    because a filter that quietly deletes a good leg is worse than one that warns.
    """
    try:
        import research
    except ImportError:
        return 0
    research.configure(cfg.get("research"))
    if research.available():
        print(f"[parlay] research off: {research.available()}")
        return 0
    date = dt.date.today().isoformat()
    done = 0
    for l in candidates(legs, cap):
        try:
            brief = research.lookup(os.path.join(HERE, "data", "v2"), date,
                                    l["league_label"], l["game"], l["pick"], l["opp"],
                                    l["ask"], [],
                                    sport_hint=SPORT_HINT.get(l["league"], "soccer"))
        except Exception as e:
            print(f"[parlay] research {l['pick']}: {type(e).__name__}")
            continue
        if not brief:
            continue
        done += 1
        heads = [{"side": h.get("side", ""), "title": h.get("title", ""),
                  "source": h.get("source", ""), "date": h.get("date", ""),
                  "watch": bool(h.get("watch"))}
                 for h in brief.get("headlines", [])][:5]
        l["research"] = {
            "checked": True,
            "pick_health": brief.get("pick_health", ""),
            "opp_health": brief.get("opp_health", ""),
            "unattributed": list(brief.get("unattributed", []))[:2],
            "headlines": heads,
        }
        if brief.get("red_flags"):
            l["flags"] = list(brief["red_flags"])[:2]
        notes = [f"could be either side: {t}" for t in brief.get("unattributed", [])[:1]]
        notes += [f"{h['side']}: {h['title']}" for h in heads if h["watch"]][:2]
        if notes:
            l["notes"] = " | ".join(notes)[:240]
    return done


SPORT_HINT = {"nfl": "football", "ncaaf": "football", "cfl": "football",
              "mlb": "baseball", "atp": "tennis", "wta": "tennis", "challenger": "tennis",
              "cricket_t20i": "cricket", "cricket_odi": "cricket",
              "cricket_test": "cricket", "cpl": "cricket"}


def snapshot(cfg=None, days=8, scope="football", leagues=None):
    """Everything the phone app needs, as one JSON blob.

    Every league is fetched once and the scopes are filters over that one board, so
    switching from NFL to Everything on the phone is instant and works with no signal.
    """
    cfg = cfg or load_config()
    kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
    legs = fetch(cfg, days, leagues)
    n = add_research(legs, cfg)
    flagged = sum(1 for l in legs if l["flags"])
    print(f"[parlay] researched {n} legs, {flagged} carry a red flag")
    print(f"[parlay] {sum(1 for l in legs if (l.get('research') or {}).get('checked'))} legs "
          f"carry news the app can show")
    legs.sort(key=lambda l: -l["p"])
    scopes = live_scopes(legs)
    if not any(sc["key"] == scope for sc in scopes):
        scope = scopes[-1]["key"] if scopes else "all"
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "days": days,
        "board": board_summary(legs),
        "legs": legs,
        "presets": PRESETS,
        "scopes": scopes,
        "scope": scope,
        "tickets": ladder(scope_legs(legs, scope)),
        "calibration_note": CALIBRATION_NOTE,
        "backtest_note": backtest_note(),
        "stats": load_stats(),
        "discount": discount_table(),
        "paper": load_paper(),
    }


# ---------------------------------------------------------------- presentation
TEMPLATE = os.path.join(HERE, "webapp", "parlay.html")
FONTS = os.path.join(HERE, "webapp", "fonts.css")


def _png(size, bg, fg, lace=(255, 248, 236)):
    """A square PNG, written by hand so the app needs no image library.

    An ellipse reads as a blob, so the ball is a lens - |y| <= h(1-(x/a)^2)^0.8 -
    which comes to a point at both tips the way a football does, with laces across
    it. Supersampled 2x2 because hard pixel edges at 192px look like a mistake.
    """
    import struct
    import zlib

    cx = cy = (size - 1) / 2.0
    a, h = size * 0.36, size * 0.225
    cos, sin = 0.9063, 0.4226                       # 25 degrees, thrown-pass tilt
    lace_len, lace_w, tick_n, tick_h = a * 0.40, size * 0.026, 4, size * 0.075

    def sample(px, py):
        dx, dy = px - cx, py - cy
        x = cos * dx + sin * dy                     # into the ball's own frame
        y = -sin * dx + cos * dy
        t = 1.0 - (x / a) ** 2
        if t <= 0 or abs(y) > h * (t ** 0.8):
            return bg
        if abs(y) <= lace_w and abs(x) <= lace_len:
            return lace                             # the long seam
        step = lace_len * 2 / (tick_n + 1)
        for i in range(tick_n):
            if abs(x - (-lace_len + step * (i + 1))) <= lace_w and abs(y) <= tick_h:
                return lace                         # a cross stitch
        return fg

    rows = bytearray()
    for py in range(size):
        rows.append(0)                              # PNG filter byte: none
        for px in range(size):
            r = g = b = 0
            for oy in (0.25, 0.75):
                for ox in (0.25, 0.75):
                    c = sample(px + ox, py + oy)
                    r += c[0]; g += c[1]; b += c[2]
            rows += bytes((r // 4, g // 4, b // 4))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def write_pwa(outdir, name="Gridiron Ticket", short="Ticket"):
    """Manifest and icons, so the page installs to a phone home screen as an app."""
    os.makedirs(outdir, exist_ok=True)
    bg, fg = (12, 18, 22), (242, 169, 59)
    for px in (192, 512):
        with open(os.path.join(outdir, f"icon-{px}.png"), "wb") as f:
            f.write(_png(px, bg, fg))
    manifest = {
        "name": name, "short_name": short,
        "description": "Kalshi parlays built to a target win chance",
        "start_url": "./", "scope": "./", "display": "standalone",
        "orientation": "portrait", "background_color": "#0C1216", "theme_color": "#0C1216",
        "icons": [{"src": f"icon-{px}.png", "sizes": f"{px}x{px}", "type": "image/png",
                   "purpose": "any maskable"} for px in (192, 512)],
    }
    with open(os.path.join(outdir, "manifest.webmanifest"), "w") as f:
        json.dump(manifest, f, indent=1)
    return ["manifest.webmanifest", "icon-192.png", "icon-512.png"]


def load_stats(path=None, keep=("generated_utc", "picks_logged", "overall", "last_7_days",
                                "by_league", "model_vs_market", "by_gate", "top_ratings",
                                "by_flow", "by_epa", "tipsters")):
    """The bot's own record, for the app's Stats tab.

    main.py has always written this and it has only ever been readable as raw JSON
    or a Discord card. model_vs_market in particular is the number that decides
    whether staking should ever come back on, and it had no UI at all.
    """
    path = path or os.path.join(HERE, "data", "v2", "stats.json")
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return {k: d[k] for k in keep if k in d}


PAPER_DIR = os.path.join(HERE, "data", "v2", "paper")


def load_paper(path=None, keep=40):
    """The paper book, trimmed to what a phone needs to show it.

    The app carries this so the Paper tab has something to render on a cold
    start; it refreshes it from the repo like everything else. Legs keep their
    entry price and their last mark, because "what did I pay" and "what is it
    worth now" are the two questions the tab exists to answer.
    """
    base = path or PAPER_DIR
    try:
        with open(os.path.join(base, "summary.json"), encoding="utf-8") as f:
            summary = json.load(f)
        with open(os.path.join(base, "ledger.json"), encoding="utf-8") as f:
            led = json.load(f)
    except (OSError, ValueError):
        return None
    tickets = led.get("tickets") or []
    openers = [t for t in tickets if t.get("status") == "open"]
    closed = sorted([t for t in tickets if t.get("status") != "open"],
                    key=lambda t: t.get("closed_utc") or "")[-keep:]

    def trim(t):
        return {"id": t["id"], "owner": t["owner"], "label": t.get("label", ""),
                "emoji": t.get("emoji", ""), "key": t.get("key", ""),
                "placed_utc": t.get("placed_utc", ""), "stake": t.get("stake"),
                "status": t.get("status"), "entry": t.get("entry"),
                "mark": t.get("mark"), "returned": t.get("returned"),
                "pnl": t.get("pnl"), "closed_utc": t.get("closed_utc"),
                "legs": [{"ticker": l["ticker"], "pick": l.get("pick", ""),
                          "opp": l.get("opp", ""), "league": l.get("league", ""),
                          "date": l.get("date", ""), "entry_ask": l.get("entry_ask"),
                          "mark_bid": l.get("mark_bid"), "result": l.get("result")}
                         for l in t.get("legs", [])]}

    try:
        with open(os.path.join(base, "calibration.json"), encoding="utf-8") as f:
            cal = json.load(f)
    except (OSError, ValueError):
        cal = None
    return {"summary": summary,
            "open": [trim(t) for t in openers][-keep:],
            "closed": [trim(t) for t in closed][::-1],
            "calibration": cal}


def backtest_note(path=None):
    """One line about how whole TICKETS have actually done, for the app's Why tab.

    Leg-level calibration and ticket-level results are different claims, and the
    app showed only the first. Reading the real backtest keeps the second honest
    instead of frozen in a hand-written sentence.
    """
    path = path or os.path.join(HERE, "data", "v2", "backtest.json")
    try:
        with open(path) as f:
            d = json.load(f)
        s = d["summary"]
    except (OSError, ValueError, KeyError):
        return ""
    return (f"Replayed on the board as it stood {d['cutoff_utc'][:10]}, {s['n']} different tickets "
            f"went {s['won']}/{s['n']} against prices that implied {s['expected_won']:.1f} - so the "
            f"prices were about right and the spread took {abs(s['roi'])*100:.0f}%. "
            + (f"One game, {s['worst_killer']['pick']}, killed {s['worst_killer']['tickets']} of them, "
               if s.get("worst_killer") else "")
            + "which is the real lesson: these tickets share legs, so one upset takes down most of them.")


def render_html(snap, template=TEMPLATE, fonts=FONTS):
    """The phone app: one self-contained HTML file with the board baked in.

    The builder is reimplemented in the page's JS so every slider recomputes
    locally - no server, no network, works from a saved file on a plane. The
    fonts are inlined for the same reason: the APK opens with no connection and
    a webfont request would leave it unstyled, so they ship as data URIs.
    """
    with open(template, encoding="utf-8") as f:
        html = f.read()
    # </script> inside the JSON would close the host <script> tag early
    blob = json.dumps(snap, separators=(",", ":")).replace("</", "<\\/")
    if "__PARLAY_DATA__" not in html:
        raise ValueError(f"{template} has no __PARLAY_DATA__ placeholder")
    html = html.replace("__PARLAY_DATA__", blob)
    if "/*__FONTS__*/" in html and os.path.exists(fonts):
        with open(fonts, encoding="utf-8") as f:
            html = html.replace("/*__FONTS__*/", f.read(), 1)
    return html


def discord_lines(snap, url=""):
    """The daily parlay card. One line per ticket, then the legs of the safest one."""
    b = snap["board"]
    sc = scope_of(snap.get("scope", "football"))
    shown = next((x for x in snap.get("scopes", []) if x["key"] == sc["key"]), None)
    head = (f"{shown['games']} games, {shown['legs']} legs" if shown
            else f"{b['games']} games, {b['legs']} legs")
    out = [f"**{sc['emoji']} {sc['label']} parlay board** — {head}, next {snap['days']} days"]
    others = [x for x in snap.get("scopes", []) if x["key"] != sc["key"]]
    if others:
        out.append("_also on the board: " +
                   ", ".join(f"{x['label']} {x['games']}" for x in others) + "_")
    if not snap["tickets"]:
        out.append("_No ticket clears the floors today — the board is too thin._")
        return out
    out.append("")
    for t in snap["tickets"]:
        out.append(f"{t['emoji']} **{t['label']}** · {t['n']} legs · wins "
                   f"**{t['win_prob']*100:.1f}%** · pays **{t['multiple']:.2f}x** "
                   f"(${t['payout_per_10']:.2f} per $10) · 1 in {t['one_in']}")
    top = snap["tickets"][0]
    out.append("")
    out.append(f"**{top['label']} legs** (weakest {top['weakest_leg']*100:.0f}c):")
    for l in sorted(top["legs"], key=lambda x: -x["p"]):
        flag = f"  ⚠ {l['flags'][0][:60]}" if l["flags"] else ""
        draw = " · draw loses" if l.get("draw") else ""
        out.append(f"{l['emoji']} {l['pick']} vs {l['opp']} — {l['p']*100:.0f}c "
                   f"(pay {l['ask']*100:.0f}c) · {l['league_label']}{draw}{flag}")
    out.append("")
    out.append("_A parlay's win chance is 1 / its payout. Extra legs buy payout, not edge — "
               "every one of them crosses another spread._")
    if url:
        out.append(f"Build your own: {url}")
    return out


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Football-only Kalshi parlay builder")
    ap.add_argument("--days", type=int, default=8, help="how far ahead to pull games")
    ap.add_argument("--scope", default="football",
                    choices=[sc["key"] for sc in SCOPES],
                    help="which board the printed ladder and the Discord card use "
                         "(the page carries them all either way)")
    ap.add_argument("--leagues", default="", help="only fetch these, e.g. nfl,ncaaf")
    ap.add_argument("--no-research", action="store_true", help="skip the injury/lineup scan")
    ap.add_argument("--json", default="", help="write the snapshot here")
    ap.add_argument("--html", default="", help="write the phone app here")
    ap.add_argument("--discord", action="store_true", help="post the card to DISCORD_WEBHOOK_URL")
    ap.add_argument("--url", default="", help="link to the hosted app, shown on the card")
    ap.add_argument("--offline", action="store_true",
                    help="skip Kalshi and emit an empty board (the app falls back to its example)")
    ap.add_argument("--from-json", dest="from_json", default="",
                    help="re-render a saved snapshot instead of pulling a new board - "
                         "how the APK build rebuilds the page without touching Kalshi")
    a = ap.parse_args(argv)

    cfg = load_config()
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            snap = json.load(f)
        # the record is regenerated rather than trusted: it is written by a
        # different job and the saved board may predate the last grading run
        snap["stats"] = load_stats()
        snap["paper"] = load_paper()
        snap["discount"] = discount_table()
        snap.setdefault("backtest_note", backtest_note())
        print(f"[parlay] re-rendering {a.from_json} "
              f"({len(snap.get('legs') or [])} legs, {snap.get('generated_utc','?')})")
    elif a.offline:
        snap = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                "days": a.days, "board": board_summary([]), "legs": [], "presets": PRESETS,
                "scopes": [], "scope": a.scope, "tickets": [],
                "calibration_note": CALIBRATION_NOTE, "backtest_note": backtest_note(),
                "stats": load_stats()}
    else:
        if a.no_research:
            cfg = dict(cfg, research=dict(cfg.get("research") or {}, mode="off"))
        leagues = [s.strip() for s in a.leagues.split(",") if s.strip()] or None
        snap = snapshot(cfg, a.days, a.scope, leagues)

    for sc in snap.get("scopes", []):
        print(f"[parlay] {sc['emoji']} {sc['label']:<11} {sc['games']:>4} games  {sc['legs']:>4} legs")
    print(f"[parlay] ladder below is the '{snap.get('scope')}' board")
    for t in snap["tickets"]:
        print(f"{t['emoji']} {t['label']:<9} {t['n']:>2} legs  win {t['win_prob']*100:6.2f}%  "
              f"pays {t['multiple']:7.2f}x  ev {t['ev']*100:+6.2f}%  weakest "
              f"{t['weakest_leg']*100:.0f}c")
    if not snap["tickets"]:
        print("[parlay] no ticket clears the floors")

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(snap, f, separators=(",", ":"))
        print(f"[parlay] wrote {a.json}")
    if a.html:
        outdir = os.path.dirname(os.path.abspath(a.html))
        os.makedirs(outdir, exist_ok=True)
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(render_html(snap))
        extra = write_pwa(outdir)
        print(f"[parlay] wrote {a.html} (+ {', '.join(extra)})")
    if a.discord:
        import notify
        notify.post("\n".join(discord_lines(snap, a.url)))
    return snap


if __name__ == "__main__":
    _cli()
