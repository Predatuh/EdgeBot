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
  5. Write data/v2/stats.json (record, ROI, CLV, Brier by tier/league) for analytics.
"""
import datetime as dt
import os
import sys
import traceback
import yaml

import kalshi, espn, elo, edge, weather, state, notify

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- history
def ingest_history(key, lg):
    """Feed every new settled Kalshi event into Elo + history. Idempotent.
    Returns (winners, closes) for grading: {event: winner}, {event: {side: close_prob}}."""
    hist = state.load_history(key)
    since = state.history_since(hist, key)
    evs = kalshi.settled_events(lg["ticker"], since)
    ratings = state.load_elo(key)
    seen = state.load_seen(key)
    winners, closes, new = {}, {}, 0
    for ev in evs:
        win = kalshi.winner(ev)
        if win is None:
            continue
        winners[ev["event"]] = win
        closes[ev["event"]] = kalshi.closing_probs(ev)
        if ev["event"] in seen:
            continue
        seen.add(ev["event"])
        teams = [s for s in ev["sides"] if not s["is_tie"]]
        if len(teams) != 2:
            continue
        # order: (away, home) for team sports so home adv is applied consistently
        teams.sort(key=lambda s: s["home"])
        a, b = teams[0]["name"], teams[1]["name"]
        draw = win == "Tie"
        sa = 0.5 if draw else (1.0 if win == a else 0.0)
        elo.update(ratings, a, b, sa, 1 - sa, k=lg.get("k", 24), use_mov=False, draw=draw)
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
    return winners, closes


# ---------------------------------------------------------------- modelling
def model_game(key, lg, cfg, ev, ratings, hist):
    """Return a dict describing the pick for one Kalshi event, or None."""
    sides = [s for s in ev["sides"] if not s["is_tie"]]
    tie = next((s for s in ev["sides"] if s["is_tie"]), None)
    if len(sides) != 2:
        return None
    sides.sort(key=lambda s: s["home"])          # [away, home] (tennis: arbitrary)
    away, home = sides[0], sides[1]
    neutral = lg.get("neutral", False)
    home_adv = 0 if neutral else lg.get("home_adv", 0)
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
    g = None
    if lg.get("espn"):
        g = espn.find_game(lg["espn"][0], lg["espn"][1], home["name"], away["name"])
    wx = None
    if g:
        if lg.get("injuries"):
            th = next((t for t in g["teams"] if espn.name_match(home["name"], t["name"])), None)
            ta = next((t for t in g["teams"] if espn.name_match(away["name"], t["name"])), None)
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

    # --- model probability ---
    p_home = elo.win_prob(ratings, home["name"], away["name"], home_adv, adj)
    games = ratings.get("_games", 0)
    low_data = games < lg.get("min_games", 50)
    if tie:
        pd = devigged[2]
        raw_home = max(0.0, min(1.0, p_home - pd / 2))
        raw_away = max(0.0, min(1.0, (1 - p_home) - pd / 2))
    else:
        raw_home, raw_away = p_home, 1 - p_home

    # --- blend with the market, shrinking harder when data is thin ---
    # The market knows things a few months of results can't. The model only gets a
    # voice proportional to how much it has actually seen of BOTH competitors.
    full = lg.get("full_conf_games", cfg.get("full_conf_games", 25))
    conf = min(1.0, min(fh[5], fa[5]) / full) if full else 1.0
    mw = cfg.get("market_weight", 0.5)
    model_w = (1 - mw) * conf
    p_home_win = model_w * raw_home + (1 - model_w) * mk_home
    p_away_win = model_w * raw_away + (1 - model_w) * mk_away
    elo_h, elo_a = ratings.get(home["name"], elo.BASE_RATING), ratings.get(away["name"], elo.BASE_RATING)
    notes.append(f"Elo {elo_h:.0f} v {elo_a:.0f}; model wt {model_w*100:.0f}%")

    # --- pick: the side with the most edge; a lean is simply the model's favorite ---
    cands = [
        {"side": home, "p": p_home_win, "mk": mk_home, "raw": raw_home, "elo": elo_h, "opp": elo_a,
         "where": "neutral" if neutral else "home"},
        {"side": away, "p": p_away_win, "mk": mk_away, "raw": raw_away, "elo": elo_a, "opp": elo_h,
         "where": "neutral" if neutral else "away"},
    ]
    for c in cands:
        c["edge"] = c["p"] - c["mk"]
    best = max(cands, key=lambda c: c["edge"])
    thr = cfg["edge_threshold"]
    max_price = cfg.get("max_price", 0.90)
    playable = (best["edge"] >= thr and best["side"]["ask"] and best["side"]["ask"] <= max_price
                and not (wx and weather.extreme(wx)))
    units = edge.kelly_units(best["p"], best["side"]["ask"], cfg["kelly_fraction"], cfg["max_units"]) \
        if playable else 0.0
    if playable and units > 0:
        tier, c = "EDGE", best
    else:
        tier, c, units = "LEAN", max(cands, key=lambda k: k["p"]), 0.0

    return {
        "pass": False, "tier": tier, "pick": c["side"], "p": c["p"], "mk": c["mk"], "edge": c["edge"],
        "raw": c["raw"], "elo_pick": c["elo"], "elo_opp": c["opp"], "where": c["where"], "conf": conf,
        "price": c["side"]["ask"], "units": units, "notes": notes, "low_data": low_data,
        "matchup": matchup, "games": games,
    }


# ---------------------------------------------------------------- main
def run_league(key, lg, cfg, body):
    winners, closes = ingest_history(key, lg)
    gw, gl = state.grade_pending(winners, closes)
    ratings = state.load_elo(key)
    hist = state.load_history(key)
    evs = kalshi.open_events(lg["ticker"], cfg.get("max_spread"))
    today = dt.date.today().isoformat()
    evs = [e for e in evs if e["date"] == today]
    print(f"[{key}] open events today: {len(evs)}; graded {gw}W/{gl}L")
    lines = []
    for ev in evs:
        r = model_game(key, lg, cfg, ev, ratings, hist)
        if not r:
            continue
        if r["pass"]:
            if cfg.get("show_passes"):
                lines.append(f"⏸️ PASS — {r['matchup']} | {r['why']}")
            continue
        icon = "🔥" if r["tier"] == "EDGE" else "📌"
        tag = " ⚠️low-data" if r["low_data"] else ""
        stake = f" | **{r['units']}u**" if r["tier"] == "EDGE" else ""
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
            "result": "", "close_prob": "", "clv": "", "profit": "",
        })
        if not logged:
            prev = state.logged_pick(ev["event"])
            if prev and (prev["pick"] != r["pick"]["name"] or prev["tier"] != r["tier"]):
                line += f"\n   ↳ ℹ️ on record from earlier run: {prev['tier']} {prev['pick']} @ {int(round(float(prev['price'] or 0)*100))}¢ (that one is tracked)"
        if r["notes"]:
            line += "\n   ↳ " + "; ".join(r["notes"])
        lines.append(line)
    if lines:
        body.append(f"\n__**{lg.get('label', key).upper()}**__ ({ratings.get('_games', 0)} games rated)")
        body.extend(lines)
    return gw, gl, state.top_ratings(ratings)


def main():
    cfg = load_config()
    kalshi.MAX_SPREAD = cfg.get("max_spread", kalshi.MAX_SPREAD)
    body = [f"🤖 **EdgeBot picks — {dt.date.today().isoformat()}** (market: Kalshi)"]
    gw = gl = 0
    errors, tops = [], {}
    for key, lg in cfg["leagues"].items():
        if not lg.get("enabled", True):
            continue
        try:
            w, l, top = run_league(key, lg, cfg, body)
            gw += w; gl += l
            tops[key] = top
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{key}: {type(e).__name__}: {str(e)[:80]}")
    s = state.record_summary()
    state.write_stats(s, tops)
    E, L = s["overall"]["EDGE"], s["overall"]["LEAN"]
    body.append(f"\n📊 **EDGE plays: {E['w']}-{E['l']} | {E['units']:+.2f}u | ROI {E['roi']}%**"
                + (f" · {E['pending']} pending" if E["pending"] else ""))
    body.append(f"📌 Leans (paper): {L['w']}-{L['l']}  · graded {gw}W/{gl}L this run")
    if E["clv_n"] or E["brier_model"] is not None:
        bits = []
        if E["avg_clv"] is not None:
            bits.append(f"avg CLV {E['avg_clv']*100:+.1f}¢ on {E['clv_n']} edges")
        if E["brier_model"] is not None:
            bits.append(f"Brier model {E['brier_model']:.3f} vs market {E['brier_market']:.3f}")
        body.append("📈 " + " · ".join(bits))
    if errors:
        body.append("⚠️ leagues skipped this run: " + "; ".join(errors))
    body.append("_🔥 = real edge vs Kalshi price, staked. 📌 = model favorite, no edge, tracked only._")
    try:
        notify.post("\n".join(body))
    except Exception as e:
        print(f"Discord post failed: {e}")
        print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
