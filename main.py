"""EdgeBot — multi-sport value picker.

Daily flow per league:
  1. Pull yesterday's finals  -> grade logged picks, update Elo ratings
  2. Pull today's board       -> model win prob (Elo + home adv + rest of
     adjustments: injuries, weather flag), compare vs market odds
  3. Post only the games with a real edge; explicitly PASS the rest
"""
import datetime as dt
import os
import sys
import yaml

import espn, elo, edge, weather, state, notify

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.yaml")) as f:
        return yaml.safe_load(f)


def ymd(d):
    return d.strftime("%Y%m%d")


def update_from_finals(lg, key):
    """Feed recent finals into Elo and grade pending picks. On the very first
    run for a league (no ratings file yet) it back-fills `warmup_days` of
    history so the model is calibrated before it ever recommends a bet."""
    finals = {}
    ratings0 = state.load_elo(key)
    days_back = lg.get("warmup_days", 45) if not ratings0 else 4
    for i in range(1, days_back + 1):
        day = dt.date.today() - dt.timedelta(days=i)
        try:
            evs = espn.parse_events(espn.scoreboard(lg["sport"], lg["league"], ymd(day)))
        except Exception as e:
            print(f"[{key}] finals fetch failed {day}: {e}")
            continue
        ratings = state.load_elo(key)
        for ev in evs:
            if not ev["completed"]:
                continue
            ratings["_games"] = ratings.get("_games", 0) + 1
            a, b = ev["a"], ev["b"]
            winner_side = None
            if a["winner"] or a["score"] > b["score"]:
                winner_side = "home" if a["home"] else "away"
            elif b["winner"] or b["score"] > a["score"]:
                winner_side = "home" if b["home"] else "away"
            draw = (not a["winner"] and not b["winner"] and a["score"] == b["score"]
                    and lg.get("draws", False))
            finals[ev["id"]] = winner_side if not draw else "draw"
            if state.seen_event(key, ev["id"]):
                continue
            elo.update(ratings, a["id"], b["id"], a["score"], b["score"],
                       k=lg.get("k", 24), use_mov=lg.get("mov", True), draw=draw)
        state.save_elo(key, ratings)
    return finals


def games_today(lg, key, cfg):
    lines = []
    ratings = state.load_elo(key)
    try:
        evs = espn.parse_events(espn.scoreboard(lg["sport"], lg["league"], ymd(dt.date.today())))
    except Exception as e:
        print(f"[{key}] board fetch failed: {e}")
        return lines
    threshold = cfg["edge_threshold"]
    for ev in evs:
        if ev["completed"] or ev["state"] == "in":
            continue
        a, b = ev["a"], ev["b"]
        home, away = (a, b) if a["home"] else (b, a)
        # neutral events (tennis) have no true home side; ESPN still labels one
        home_adv = lg.get("home_adv", 0) if lg.get("home_matters", True) else 0

        # ---- adjustments ("everything we can pull") ----
        notes = []
        adj = 0.0
        if lg.get("injuries", False):
            out_h = espn.team_out_count(lg["sport"], lg["league"], home["id"])
            out_a = espn.team_out_count(lg["sport"], lg["league"], away["id"])
            adj -= (out_h - out_a) * lg.get("injury_elo", 12)
            if out_h or out_a:
                notes.append(f"OUT: {home['name']} {out_h} / {away['name']} {out_a}")
        wx = None
        if lg.get("weather", False) and ev.get("indoor") is not True:
            wx = weather.forecast(ev.get("city"))
            if wx:
                notes.append(f"wx {weather.describe(wx)}")

        p_home = elo.win_prob(ratings, home["id"], away["id"], home_adv, adj)
        cold_start = ratings.get("_games", 0) < lg.get("min_games", 60)

        o = ev.get("odds") or {}
        if lg.get("draws") and o.get("draw_ml") is not None:
            side, eg, ml, p_home = edge.evaluate_soccer(
                p_home, o.get("home_ml"), o.get("away_ml"), o.get("draw_ml"), threshold)
        else:
            side, eg, ml = edge.evaluate(p_home, o.get("home_ml"), o.get("away_ml"), threshold)

        matchup = f'{away["name"]} @ {home["name"]}' if lg.get("home_matters", True) \
            else f'{a["name"]} vs {b["name"]}'

        no_odds = not o or o.get("home_ml") is None or o.get("away_ml") is None

        if side and not cold_start and not (wx and weather.extreme(wx) and cfg.get("skip_extreme_wx", True)):
            pick_team = home if side == "home" else away
            p = p_home if side == "home" else 1 - p_home
            units = edge.kelly_units(p, ml, cfg["kelly_fraction"], cfg["max_units"])
            if units <= 0:
                continue
            lines.append(f'✅ **{pick_team["name"]} ML {int(ml):+d}** — {matchup}'
                         f' | model {p*100:.0f}% vs mkt, edge +{eg*100:.1f}%, {units}u'
                         + (f' | {"; ".join(notes)}' if notes else ""))
            state.append_pick({
                "date": dt.date.today().isoformat(), "league": key,
                "event_id": ev["id"], "matchup": matchup,
                "pick": f'{pick_team["name"]} ML', "side": side,
                "model_prob": round(p, 3), "edge": round(eg, 3), "ml": ml,
                "units": units, "result": "", "profit": "",
            })
        else:
            why = "cold start (building ratings)" if cold_start else \
                  ("no market odds posted" if no_odds else
                   ("extreme weather" if (wx and weather.extreme(wx)) else
                    f"no edge (best {eg*100:+.1f}%)"))
            if cfg.get("show_passes", True):
                lines.append(f'⏸️ PASS — {matchup} | {why}')
    return lines


def main():
    cfg = load_config()
    all_finals_graded_w = all_finals_graded_l = 0
    body = [f'🤖 **EdgeBot picks — {dt.date.today().isoformat()}**']
    for key, lg in cfg["leagues"].items():
        if not lg.get("enabled", False):
            continue
        finals = update_from_finals(lg, key)
        w, l = state.grade_pending(finals)
        all_finals_graded_w += w
        all_finals_graded_l += l
        lines = games_today(lg, key, cfg)
        if lines:
            body.append(f'\n__**{lg.get("label", key).upper()}**__')
            body.extend(lines)
    s = state.record_summary()
    body.append(f'\n📊 **Record {s["w"]}-{s["l"]} | {s["units"]:+.2f}u | ROI {s["roi"]}%**'
                f' (graded {all_finals_graded_w}W/{all_finals_graded_l}L today)')
    body.append('_Picks only fire when model edge ≥ threshold. No edge = no bet._')
    notify.post("\n".join(body))


if __name__ == "__main__":
    sys.exit(main())
