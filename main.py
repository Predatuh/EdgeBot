"""EdgeBot v2 — Kalshi-first multi-sport value picker.

Per league (a Kalshi series):
  1. Pull settled Kalshi results (incrementally after the first run) -> Elo ratings
     + game history (form, H2H, rest), and auto-grade any logged picks that settled
  2. Pull today's open Kalshi events with live prices
  3. Model each game: Elo + home adv + form + rest + injuries (ESPN) + weather
  4. Post every game with a projected winner:
       🔥 EDGE  = model beats the Kalshi price by >= threshold  (staked, tracked in units)
       📌 LEAN  = model's favorite but no real edge             (paper pick, W-L only)
     A game is only PASSED when it has no usable price / liquidity.
  5. Web-research edges and near-edges with Claude + web search (research.py):
     injuries, lineups, form, situational factors -> card notes, a capped Elo nudge,
     and a red-flag demotion so a known problem never gets staked.
  6. Write data/v2/stats.json (record, ROI, CLV, Brier by tier/league/research) for analytics.

Run `python main.py --grade-only [--days=N]` for a results-only check: it grades
picks that have settled and posts a W/L + units + CLV card, without logging any
new picks (so it is safe to run at any hour).
"""
import datetime as dt
import os
import sys
import traceback
import yaml

import kalshi, espn, elo, edge, weather, state, notify, research

HERE = os.path.dirname(os.path.abspath(__file__))
# All-Star style events: not real teams, keep them out of the ratings and the card.
EXHIBITION = {"AL", "NL", "American League", "National League", "AFC", "NFC", "East", "West"}


def neutral_league(lg):
    return bool(lg.get("neutral")) or ticker_order(lg) == "neutral"


def ticker_order(lg):
    """How this series orders the two codes in its event ticker. US series are
    AWAY+HOME (suffix = home); every Kalshi soccer series is HOME+AWAY."""
    return lg.get("ticker_order", "neutral" if lg.get("neutral") else "away_home")


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- history
def ingest_history(key, lg):
    """Feed every new settled Kalshi event into Elo + history. Idempotent.
    Returns {event: winner} for grading ('VOID' for events that resolved with no winner)."""
    hist = state.load_history(key)
    since = state.history_since(hist, key)
    evs = kalshi.settled_events(lg["ticker"], since, ticker_order(lg))
    ratings = state.load_elo(key)
    seen = state.load_seen(key)
    winners, new = {}, 0
    for ev in evs:
        win = kalshi.winner(ev)
        if win is None:
            continue
        winners[ev["event"]] = win
        if win == "VOID" or ev["event"] in seen:
            continue                    # a voided event grades the pick but teaches nothing
        teams = [s for s in kalshi.match_sides(ev) if not s["is_tie"]]
        if len(teams) != 2 or any(s["name"] in EXHIBITION for s in teams):
            continue                    # mark seen only once we've actually rated it, so a
                                        # parsing bug can't permanently swallow the event
        seen.add(ev["event"])
        # order: (away, home) for team sports so home adv is applied consistently
        teams.sort(key=lambda s: s["home"])
        a, b = teams[0]["name"], teams[1]["name"]
        draw = win == "Tie"
        sa = 0.5 if draw else (1.0 if win == a else 0.0)
        # home advantage belongs in the expected score during training too, or ratings
        # absorb each side's home/away schedule imbalance (b is the home side)
        elo.update(ratings, a, b, sa, 1 - sa, k=lg.get("k", 24), use_mov=False, draw=draw,
                   home_adv_b=0.0 if neutral_league(lg) else lg.get("home_adv", 0))
        ratings["_games"] = ratings.get("_games", 0) + 1
        hist.append({"d": ev["date"], "a": a, "b": b, "w": win})
        new += 1
    if new:
        hist.sort(key=lambda g: g["d"])
    state.save_elo(key, ratings)
    state.save_history(key, hist)
    state.save_seen(key, seen)
    print(f"[{key}] settled: {len(evs)} fetched, {new} new, {ratings.get('_games', 0)} rated"
          f"{' (incremental)' if since else ' (full history)'}")
    return winners


# ---------------------------------------------------------------- modelling
def model_game(key, lg, cfg, ev, ratings, hist):
    """Return a dict describing the pick for one Kalshi event, or None."""
    msides = kalshi.match_sides(ev)
    sides = [s for s in msides if not s["is_tie"]]
    tie = next((s for s in msides if s["is_tie"]), None)
    if len(sides) != 2 or any(s["name"] in EXHIBITION for s in sides):
        return None
    sides.sort(key=lambda s: s["home"])          # [away, home] (tennis: arbitrary)
    away, home = sides[0], sides[1]
    neutral = lg.get("neutral", False)
    home_adv = 0 if neutral else lg.get("home_adv", 0)

    # Settle who is at home BEFORE anything reads the ordering (form, rest, injuries,
    # market sides all depend on it). The ticker's code order is only a convention and
    # it varies by sport - it was inverted for every soccer game - so when ESPN has the
    # fixture its homeAway flag wins.
    g = espn.find_game(lg["espn"][0], lg["espn"][1], home["name"], away["name"]) if lg.get("espn") else None
    espn_note = ""
    th = ta = None
    if g:
        th = next((t for t in g["teams"] if espn.name_match(home["name"], t["name"])), None)
        ta = next((t for t in g["teams"] if espn.name_match(away["name"], t["name"])), None)
        if th and ta and not neutral and th["home"] != ta["home"] and not th["home"]:
            home, away = away, home
            th, ta = ta, th
            espn_note = f"ESPN says {home['name']} is home (ticker disagreed)"
            print(f"[{key}] ticker_order looks wrong: ESPN has {home['name']} at home v {away['name']}")
    matchup = f"{away['name']} vs {home['name']}" if neutral else f"{away['name']} @ {home['name']}"

    # --- market (Kalshi) first: no usable price = nothing to beat ---
    probs = [home["prob"], away["prob"]] + ([tie["prob"]] if tie else [])
    if any(p is None for p in probs):
        return {"pass": True, "why": "no live price / illiquid", "matchup": matchup}
    devigged = edge.devig(probs)
    mk_home, mk_away = devigged[0], devigged[1]

    notes, adj = [], 0.0

    # --- form / streak / rest (from Kalshi history) ---
    fh = state.form(hist, home["name"]); fa = state.form(hist, away["name"])
    if fh[5] or fa[5]:
        notes.append(f"form {home['name']} {fh[3] or '-'} / {away['name']} {fa[3] or '-'}")
    if fh[4] is not None and fa[4] is not None and not neutral:
        diff = fh[4] - fa[4]
        if abs(diff) >= 2:
            adj += max(-15, min(15, diff * 4))   # rested team gets a nudge
            notes.append(f"rest {home['name']} {fh[4]}d / {away['name']} {fa[4]}d")
    hw, hl, hn = state.h2h(hist, home["name"], away["name"])
    if hn:
        notes.append(f"H2H {home['name']} {hw}-{hl}")

    # --- ESPN enrichment: injuries, venue, weather ---
    wx, full_names = None, {}
    if espn_note:
        notes.append(espn_note)
    if g:
        full_names = {s["name"]: t["name"] for s, t in ((home, th), (away, ta)) if t}   # 'Philadelphia' -> 'Philadelphia Eagles'
        if lg.get("injuries"):
            if th and ta:
                oh = espn.team_out_count(lg["espn"][0], lg["espn"][1], th["id"])
                oa = espn.team_out_count(lg["espn"][0], lg["espn"][1], ta["id"])
                adj -= (oh - oa) * lg.get("injury_elo", 10)
                if oh or oa:
                    notes.append(f"OUT {home['name']} {oh} / {away['name']} {oa}")
        if g.get("venue"):
            notes.append(f"@ {g['venue']}")
        if lg.get("weather") and g.get("indoor") is not True and g.get("city"):
            wx = weather.forecast(g["city"])
            if wx:
                notes.append(f"wx {weather.describe(wx)}")

    # --- model probability (pure Elo + adjustments) ---
    games = ratings.get("_games", 0)
    low_data = games < lg.get("min_games", 50)
    full = lg.get("full_conf_games", cfg.get("full_conf_games", 25))
    conf = min(1.0, min(fh[5], fa[5]) / full) if full else 1.0
    mw = cfg.get("market_weight", 0.5)
    model_w = (1 - mw) * conf
    elo_h, elo_a = ratings.get(home["name"], elo.BASE_RATING), ratings.get(away["name"], elo.BASE_RATING)
    thr = cfg["edge_threshold"]
    max_price = cfg.get("max_price", 0.90)
    min_price = cfg.get("min_price", 0.0)
    max_disagree = cfg.get("max_disagreement", 1.0)
    require_rating_edge = cfg.get("require_rating_edge", False)
    staking = cfg.get("staking", False) and lg.get("stake", True)

    def decide(extra_home_adj):
        """Blend model + market for a given extra Elo adjustment on the home side,
        pick the side with the most edge (a lean is simply the model favorite)."""
        p_home = elo.win_prob(ratings, home["name"], away["name"], home_adv, adj + extra_home_adj)
        if tie:
            pd = devigged[2]
            raw_home = max(0.0, min(1.0, p_home - pd / 2))
            raw_away = max(0.0, min(1.0, (1 - p_home) - pd / 2))
        else:
            raw_home, raw_away = p_home, 1 - p_home
        cands = [
            {"side": home, "raw": raw_home, "mk": mk_home, "elo": elo_h, "opp": elo_a,
             "where": "neutral" if neutral else "home", "sign": 1},
            {"side": away, "raw": raw_away, "mk": mk_away, "elo": elo_a, "opp": elo_h,
             "where": "neutral" if neutral else "away", "sign": -1},
        ]
        for c in cands:
            c["p"] = model_w * c["raw"] + (1 - model_w) * c["mk"]
            c["edge"] = c["p"] - c["mk"]
        best = max(cands, key=lambda c: c["edge"])
        ask = best["side"]["ask"]
        # The gate must use the price we actually pay. edge is measured against the
        # de-vigged mid, but the stake is bought at the ask, so a pick could clear 4%
        # of mid value with less than 4% at the executable price.
        exec_edge = (best["p"] - ask) if ask else -1.0
        disagree = abs(best["raw"] - best["mk"])
        gate = ""
        if exec_edge < thr:
            gate = "edge"
        elif not ask or ask > max_price:
            gate = "price_cap"
        elif ask < min_price:
            gate = "price_floor"
        elif disagree > max_disagree:
            # edge = (1-mw)*conf*(raw-mk): with a shrunken model weight, clearing the
            # threshold REQUIRES a huge raw-vs-market gap, which an unseparated Elo can
            # only produce against heavy favourites. Pooled over 306 graded picks,
            # raw-mk >= 0.10 went 2-24 against 6.2 market-implied wins.
            gate = "disagreement"
        elif require_rating_edge and best["elo"] <= best["opp"] and not neutral_league(lg):
            gate = "rating"             # the raw model must itself rate our side higher
        elif wx and weather.extreme(wx):
            gate = "weather"
        units = edge.kelly_units(best["p"], ask, cfg["kelly_fraction"], cfg["max_units"]) if not gate else 0.0
        if not gate and units > 0:
            return "EDGE", best, (units if staking else 0.0), ""
        return "LEAN", max(cands, key=lambda k: k["p"]), 0.0, (gate or "kelly")

    tier, c, units, gate = decide(0.0)

    # --- web research on the pick (edges and near-edges only; capped per run) ---
    brief, r_adj = None, 0.0
    if research.eligible(tier, c["edge"], thr):
        opp = away if c["side"] is home else home
        brief = research.lookup(state.DATA, dt.date.today().isoformat(), lg.get("label", key), matchup,
                                c["side"]["name"], opp["name"], c["side"]["ask"], notes,
                                sport_hint=" ".join(lg["espn"]) if lg.get("espn") else "", aliases=full_names)
        if brief:
            researched = c["side"]
            r_adj = research.elo_adjust(brief)
            if r_adj:
                # the nudge is on the PICK; convert to a home-side adjustment
                tier, c, units, gate = decide(r_adj * c["sign"])
            if c["side"] is not researched:
                # research moved us onto the other side: the brief's lean/flags were
                # about the side we now fade, so log the lean relative to the new pick
                brief = dict(brief, lean=-brief["lean"], red_flags=[])
                r_adj = -r_adj          # log the nudge relative to the side we ended on
                notes.append(f"research flipped pick off {researched['name']}")
            elif tier == "EDGE" and research.red_flag(brief):
                tier, units, gate = "LEAN", 0.0, "research_flag"
                notes.append("research red flag: not staked")

    notes.append(f"Elo {elo_h:.0f} v {elo_a:.0f}; model wt {model_w*100:.0f}%"
                 + (f"; research {r_adj:+.0f} Elo" if r_adj else ""))

    raw_home_final = elo.win_prob(ratings, home["name"], away["name"], home_adv, adj)
    model_fav = home["name"] if raw_home_final >= 0.5 else away["name"]
    return {
        "pass": False, "tier": tier, "pick": c["side"], "p": c["p"], "mk": c["mk"], "edge": c["edge"],
        "raw": c["raw"], "elo_pick": c["elo"], "elo_opp": c["opp"], "where": c["where"], "conf": conf,
        "price": c["side"]["ask"], "units": units, "notes": notes, "low_data": low_data,
        "matchup": matchup, "games": games, "brief": brief, "r_adj": r_adj,
        "gate": gate, "staked": 1 if units > 0 else 0, "model_fav": model_fav,
        "model_fav_won": None,
    }


# ---------------------------------------------------------------- main
def run_league(key, lg, cfg, body, grade_only=False):
    winners = ingest_history(key, lg)
    gw, gl = state.grade_pending(winners)
    state.grade_tips(winners)
    ratings = state.load_elo(key)
    # Every run snapshots the live price of any pick that has not settled yet. The
    # last snapshot before an event starts is the closest thing to a closing line we
    # can observe; the settled market's last trade is the RESULT, not a close.
    evs = kalshi.open_events(lg["ticker"], cfg.get("max_spread"), ticker_order(lg))
    snap = {ev["event"]: {s["name"]: s["prob"] for s in kalshi.match_sides(ev) if s["prob"] is not None}
            for ev in evs}
    n_snap = state.snapshot_open_prices(snap) + state.snapshot_open_prices(snap, tips=True)
    if grade_only:                      # results check: grade + rate, never log new picks
        print(f"[{key}] graded {gw}W/{gl}L; {n_snap} live price snapshot(s)")
        return gw, gl, state.top_ratings(ratings)
    hist = state.load_history(key)
    today = dt.date.today().isoformat()
    evs = [e for e in evs if e["date"] == today]
    print(f"[{key}] open events today: {len(evs)}; graded {gw}W/{gl}L")
    compact = (cfg.get("card") or {}).get("compact_leans", True)
    max_lean_lines = (cfg.get("card") or {}).get("max_lean_lines", 3)
    lines, leans = [], []
    for ev in evs:
        r = model_game(key, lg, cfg, ev, ratings, hist)
        if not r:
            continue
        if r["pass"]:
            if cfg.get("show_passes"):
                lines.append(f"⏸️ PASS — {r['matchup']} | {r['why']}")
            continue
        icon = ("🔥" if r["staked"] else "🧪") if r["tier"] == "EDGE" else "📌"
        tag = " ⚠️low-data" if r["low_data"] else ""
        stake = (f" | **{r['units']}u**" if r["staked"]
                 else " | _paper_" if r["tier"] == "EDGE" else "")
        line = (f"{icon} **{r['pick']['name']}** @ {int(round(r['price']*100))}¢ — {r['matchup']}"
                f" | model {r['p']*100:.0f}% vs Kalshi {r['mk']*100:.0f}% ({r['edge']*100:+.1f}%){stake}{tag}")
        now = dt.datetime.now(dt.timezone.utc)
        logged = state.append_pick({
            "date": today, "time_utc": now.strftime("%H:%M"), "league": key,
            "event_id": ev["event"], "matchup": r["matchup"],
            "pick": r["pick"]["name"], "side": r["where"], "tier": r["tier"],
            "model_raw": round(r["raw"], 3), "model_prob": round(r["p"], 3),
            "market_prob": round(r["mk"], 3), "edge": round(r["edge"], 3),
            "price": r["price"], "units": r["units"],
            "elo_pick": round(r["elo_pick"], 1), "elo_opp": round(r["elo_opp"], 1),
            "conf": round(r["conf"], 2), "notes": " | ".join(r["notes"]),
            "research": (r["brief"] or {}).get("summary", ""),
            "research_lean": (r["brief"] or {}).get("lean", ""),
            "research_adj": r["r_adj"] or "",
            "research_flag": "; ".join((r["brief"] or {}).get("red_flags", [])),
            "staked": r["staked"], "gate": r["gate"], "model_fav": r["model_fav"],
            "result": "", "graded_utc": "", "close_prob": "", "close_utc": "", "clv": "", "profit": "",
        })
        if not logged:
            prev = state.logged_pick(ev["event"])
            if prev and (prev["pick"] != r["pick"]["name"] or prev["tier"] != r["tier"]):
                line += f"\n   ↳ ℹ️ on record from earlier run: {prev['tier']} {prev['pick']} @ {int(round(float(prev['price'] or 0)*100))}¢ (that one is tracked)"
        # Compact only the unremarkable leans. A pick that research demoted, or that a
        # staking gate stopped, still explains itself - that reason is the whole point.
        notable = bool((r["brief"] or {}).get("red_flags")) or r["gate"] in ("disagreement", "rating")
        if r["tier"] == "EDGE" or notable or not compact:
            if r["notes"]:
                line += "\n   ↳ " + "; ".join(r["notes"])
            for rl in research.card_lines(r["brief"]):
                line += "\n   ↳ " + rl
            lines.append(line)
        else:
            # compact: leans are the market's favourites, so they get one line each,
            # no notes and no headlines (a 155-pick day was ~25 Discord messages)
            leans.append(f"{r['pick']['name']} @{int(round(r['price']*100))}¢")
            continue
    if leans:
        per = 6
        rows = [" · ".join(leans[i:i + per]) for i in range(0, len(leans), per)][:max_lean_lines]
        shown = min(len(leans), per * max_lean_lines)
        lines.append(f"📌 market favourites ({len(leans)}): " + "\n   " + "\n   ".join(rows)
                     + (f"\n   …and {len(leans) - shown} more (full list in data/v2/picks_log.csv)"
                        if len(leans) > shown else ""))
    if lines:
        body.append(f"\n__**{lg.get('label', key).upper()}**__ ({ratings.get('_games', 0)} games rated)")
        body.extend(lines)
    return gw, gl, state.top_ratings(ratings)


def grade_outside_leagues(cfg, done):
    """Grade tipster picks in leagues we don't bet ourselves (Challenger, say).
    Their settlements never come through our own ingest loop, so without this
    those rows would sit pending for ever."""
    tips = state.read_tips()
    want = {r["league"] for r in tips if not r["result"] and r["league"]} - done
    gw = gl = 0
    for key in sorted(want):
        lg = (cfg.get("leagues") or {}).get(key)
        if not lg:
            continue
        dates = [r["date"] for r in tips if r["league"] == key and not r["result"] and r["date"]]
        try:
            since = state._ts(dt.date.fromisoformat(min(dates)) - dt.timedelta(days=1)) if dates else None
            evs = kalshi.settled_events(lg["ticker"], since, ticker_order(lg))
        except Exception as e:
            print(f"[{key}] outside-league settle fetch failed: {type(e).__name__}: {str(e)[:70]}")
            continue
        winners = {ev["event"]: kalshi.winner(ev) for ev in evs if kalshi.winner(ev) is not None}
        w, l = state.grade_tips(winners)
        gw += w; gl += l
        print(f"[{key}] outside league: {len(evs)} settled, graded {w}W/{l}L of his picks")
    return gw, gl


def status_body(days):
    """Results card: how the last `days` days of picks actually did."""
    rows = state.read_log()
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    # A pick made on Monday that settles on Wednesday used to appear on no card at
    # all, because selection was by pick date. Graded rows are selected by when they
    # were graded; pending rows still by pick date.
    graded = [r for r in rows if r["result"] in ("W", "L")
              and (r.get("graded_utc", "")[:10] or r["date"]) >= since]
    recent = graded + [r for r in rows if not r["result"] and r["date"] >= since]
    out = [f"📋 **EdgeBot results — {since} to {dt.date.today().isoformat()}**"]
    for tier, icon in (("EDGE", "🔥"), ("LEAN", "📌")):
        rs = [r for r in graded if r["tier"] == tier]
        rs.sort(key=lambda r: (r.get("graded_utc", ""), r["league"]))
        pend = [r for r in recent if r["tier"] == tier and not r["result"]]
        if not rs and not pend:
            continue
        w = sum(1 for r in rs if r["result"] == "W")
        pl = sum(state._fl(r["profit"]) for r in rs)
        head = f"\n{icon} **{tier}: {w}-{len(rs) - w}**"
        if tier == "EDGE":
            head += f" | {pl:+.2f}u"
        out.append(head + (f" · {len(pend)} still pending" if pend else ""))
        for r in sorted(rs, key=lambda r: (r["date"], r["league"])):
            mark = "✅" if r["result"] == "W" else "❌"
            money = f" {state._fl(r['profit']):+.2f}u" if tier == "EDGE" else ""
            clv = f" · CLV {state._fl(r['clv'])*100:+.0f}¢" if r["clv"] != "" and r.get("close_utc") else ""
            when = f" ({r['date'][5:]})" if r.get("graded_utc", "")[:10] != r["date"] else ""
            out.append(f"{mark} {r['pick']} @ {int(round(state._fl(r['price'])*100))}¢{money}{when} — {r['matchup']}{clv}")
        for r in sorted(pend, key=lambda r: -state._fl(r["edge"]))[:8 if tier == "EDGE" else 0]:
            out.append(f"⏳ {r['pick']} @ {int(round(state._fl(r['price'])*100))}¢ — {r['matchup']}")
    tips = state.tipster_summary()
    for name, t in tips.items():
        o, ag, dis = t["overall"], t["agrees_with_model"], t["disagrees_with_model"]
        if not o["n"] and not o["pending"]:
            continue
        line = f"\n👤 **{name}: {o['w']}-{o['l']}** | ${o['profit_100']:+,.2f} per $100 flat | ROI {o['roi']}%"
        if o["pending"]:
            line += f" · {o['pending']} pending"
        out.append(line)
        if o["expected_w"] is not None:
            out.append(f"   market priced those picks for ~{o['expected_w']} wins in {o['n']}")
        if dis["n"]:
            out.append(f"   vs our model — agree {ag['w']}-{ag['l']}, disagree {dis['w']}-{dis['l']} (ROI {dis['roi']}%)")
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    grade_only = "--grade-only" in argv or "--status" in argv
    days = 2
    for a in argv:
        if a.startswith("--days="):
            days = max(1, int(a.split("=", 1)[1]))
    cfg = load_config()
    kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
    research.configure(cfg.get("research"))
    off = "" if grade_only else research.available()
    if not grade_only:
        print(f"[research] {'off: ' + off if off else 'on: ' + research.label()}")
    body = [] if grade_only else [f"🤖 **EdgeBot picks — {dt.date.today().isoformat()}** (market: Kalshi)"]
    gw = gl = 0
    errors, tops = [], {}
    for key, lg in cfg["leagues"].items():
        if not lg.get("enabled", True):
            continue
        try:
            w, l, top = run_league(key, lg, cfg, body, grade_only)
            gw += w; gl += l
            tops[key] = top
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{key}: {type(e).__name__}: {str(e)[:80]}")
    grade_outside_leagues(cfg, {k for k, v in cfg["leagues"].items() if v.get("enabled", True)})
    state.link_own_picks()          # tipster slates logged before today's picks existed
    s = state.record_summary()
    state.write_stats(s, tops)
    E, L = s["overall"]["EDGE"], s["overall"]["LEAN"]
    if grade_only:
        body = status_body(days)
        body.append(f"\n📊 **All-time EDGE: {E['w']}-{E['l']} | {E['units']:+.2f}u | ROI {E['roi']}%**"
                    + (f" · {E['pending']} pending" if E["pending"] else ""))
    else:
        body.append(f"\n📊 **EDGE plays: {E['w']}-{E['l']} | {E['units']:+.2f}u | ROI {E['roi']}%**"
                + (f" · {E['pending']} pending" if E["pending"] else ""))
        body.append(f"📌 Leans (paper): {L['w']}-{L['l']}  · graded {gw}W/{gl}L this run")
    mvm = s.get("model_vs_market", {}).get("model_likes_our_side_a_lot (d>0.10)", {})
    allr = state._stats([r for r in state.read_log()])
    bits = []
    if allr.get("brier_raw") is not None:
        bits.append(f"model Brier {allr['brier_raw']:.3f} vs market {allr['brier_market']:.3f} "
                    f"({'model ahead' if allr['brier_raw'] < allr['brier_market'] else 'market ahead'}, n={allr['n']})")
    if mvm.get("n"):
        bits.append(f"where the model disagrees most: {mvm['w']}-{mvm['l']} vs {mvm['expected_w']} the market implied")
    if E["clv_n"]:
        bits.append(f"avg CLV {E['avg_clv']*100:+.1f}¢ on {E['clv_n']} priced-and-snapshotted")
    if bits:
        body.append("📈 " + " · ".join(bits))
    if not cfg.get("staking", False):
        body.append("🧪 _PAPER MODE — no money staked. Edges are logged and graded so the "
                    "filter keeps being measured; set `staking: true` in config.yaml to arm it._")
    if errors:
        body.append("⚠️ leagues skipped this run: " + "; ".join(errors))
    if grade_only:
        body.append(f"_graded {gw}W/{gl}L this check · CLV = price move after we bet; positive means we beat the close._")
    else:
        if off:
            body.append(f"_🔎 research off ({off})_")
        elif research.run_note():
            body.append(f"_🔎 {research.run_note()}_")
        body.append("_🔥 = real edge vs Kalshi price, staked. 📌 = model favorite, no edge, tracked only._")
    try:
        notify.post("\n".join(body))
    except Exception as e:
        print(f"Discord post failed: {e}")
        print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
