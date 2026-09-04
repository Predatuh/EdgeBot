"""EdgeBot v2 — Kalshi-first multi-sport value picker.

Per league (a Kalshi series):
  1. Pull ALL settled Kalshi results -> Elo ratings + game history (form, H2H, rest)
     and auto-grade any logged picks that have now settled
  2. Pull today's open Kalshi events with live prices
  3. Model each game: Elo + home adv + form + rest + injuries (ESPN) + weather
  4. Post every game with a projected winner:
       🔥 EDGE  = model beats the Kalshi price by >= threshold  (staked, tracked in units)
       📌 LEAN  = model's favorite but no real edge             (paper pick, W-L only)
     A game is only PASSED when it has no usable price / liquidity.
"""
import datetime as dt
import os
import sys
import yaml

import kalshi, espn, elo, edge, weather, state, notify

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- history
def ingest_history(key, lg):
    """Feed every settled Kalshi event into Elo + history. Idempotent."""
    winners = {}
    try:
        evs = kalshi.settled_events(lg["ticker"])
    except Exception as e:
        print(f"[{key}] settled fetch failed: {e}")
        return winners
    ratings = state.load_elo(key)
    hist = state.load_history(key)
    for ev in evs:
        win = kalshi.winner(ev)
        if win is None:
            continue
        winners[ev["event"]] = win
        if state.seen_event(key, ev["event"]):
            continue
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
    state.save_elo(key, ratings)
    state.save_history(key, hist)
    return winners


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

    # --- market (Kalshi) ---
    probs = devigged = [home["prob"], away["prob"]] + ([tie["prob"]] if tie else [])
    if any(p is None for p in probs):
        return {"pass": True, "why": "no live price", "matchup": f"{away['name']} vs {home['name']}"}
    devigged = edge.devig(probs)
    mk_home, mk_away = devigged[0], devigged[1]
    if tie:
        pd = devigged[2]
        p_home_win = max(0.0, min(1.0, p_home - pd / 2))
        p_away_win = max(0.0, min(1.0, (1 - p_home) - pd / 2))
    else:
        p_home_win, p_away_win = p_home, 1 - p_home

    # --- blend with the market, shrinking harder when data is thin ---
    # The market knows things 2 months of results can't. The model only gets a
    # voice proportional to how much it has actually seen of BOTH competitors.
    full = lg.get("full_conf_games", 25)
    conf = min(1.0, min(fh[5], fa[5]) / full) if full else 1.0
    mw = cfg.get("market_weight", 0.5)
    model_w = (1 - mw) * conf
    p_home_win = model_w * p_home_win + (1 - model_w) * mk_home
    p_away_win = model_w * p_away_win + (1 - model_w) * mk_away
    notes.append(f"Elo {ratings.get(home['name'],1500):.0f} v {ratings.get(away['name'],1500):.0f}; model wt {model_w*100:.0f}%")

    eh, ea = p_home_win - mk_home, p_away_win - mk_away
    if eh >= ea:
        pick, p, mk, eg = home, p_home_win, mk_home, eh
    else:
        pick, p, mk, eg = away, p_away_win, mk_away, ea

    thr = cfg["edge_threshold"]
    max_price = cfg.get("max_price", 0.90)
    tier = "EDGE" if (eg >= thr and pick["ask"] and pick["ask"] <= max_price
                      and not (wx and weather.extreme(wx))) else "LEAN"
    if tier == "LEAN" and eg < thr and (p < 0.5):
        # model's own favorite is the other side; show that as the lean instead
        pick, p, mk, eg = (home, p_home_win, mk_home, eh) if p_home_win >= p_away_win \
            else (away, p_away_win, mk_away, ea)
    units = edge.kelly_units(p, pick["ask"], cfg["kelly_fraction"], cfg["max_units"]) if tier == "EDGE" else 0.0
    if tier == "EDGE" and units <= 0:
        tier = "LEAN"

    return {
        "pass": False, "tier": tier, "pick": pick, "p": p, "mk": mk, "edge": eg,
        "price": pick["ask"], "units": units, "notes": notes, "low_data": low_data,
        "matchup": f"{away['name']} vs {home['name']}" if neutral else f"{away['name']} @ {home['name']}",
        "elo_pick": ratings.get(pick["name"], 1500), "games": games,
    }


# ---------------------------------------------------------------- main
def run_league(key, lg, cfg, body):
    winners = ingest_history(key, lg)
    gw, gl = state.grade_pending(winners)
    ratings = state.load_elo(key)
    hist = state.load_history(key)
    try:
        evs = kalshi.open_events(lg["ticker"])
    except Exception as e:
        print(f"[{key}] open fetch failed: {e}")
        return gw, gl
    today = dt.date.today().isoformat()
    evs = [e for e in evs if e["date"] == today]
    if not evs:
        return gw, gl
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
        if r["notes"]:
            line += "\n   ↳ " + "; ".join(r["notes"])
        lines.append(line)
        state.append_pick({
            "date": today, "league": key, "event_id": ev["event"], "matchup": r["matchup"],
            "pick": r["pick"]["name"], "tier": r["tier"],
            "model_prob": round(r["p"], 3), "market_prob": round(r["mk"], 3),
            "edge": round(r["edge"], 3), "price": r["price"], "units": r["units"],
            "result": "", "profit": "",
        })
    if lines:
        body.append(f"\n__**{lg.get('label', key).upper()}**__ ({ratings.get('_games', 0)} games rated)")
        body.extend(lines)
    return gw, gl


def main():
    cfg = load_config()
    body = [f"🤖 **EdgeBot picks — {dt.date.today().isoformat()}** (market: Kalshi)"]
    gw = gl = 0
    for key, lg in cfg["leagues"].items():
        if not lg.get("enabled", True):
            continue
        try:
            w, l = run_league(key, lg, cfg, body)
            gw += w; gl += l
        except Exception as e:
            print(f"[{key}] failed: {e}")
    s = state.record_summary()
    body.append(f"\n📊 **EDGE plays: {s['EDGE']['w']}-{s['EDGE']['l']} | {s['EDGE']['units']:+.2f}u | ROI {s['EDGE']['roi']}%**")
    body.append(f"📌 Leans (paper): {s['LEAN']['w']}-{s['LEAN']['l']}  · graded {gw}W/{gl}L this run")
    body.append("_🔥 = real edge vs Kalshi price, staked. 📌 = model favorite, no edge, tracked only._")
    notify.post("\n".join(body))


if __name__ == "__main__":
    sys.exit(main())
