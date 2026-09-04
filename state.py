"""Persistence: Elo tables and the pick log live as files in the repo and are
committed back by the GitHub Action after each run — that's the 'memory'."""
import csv
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG = os.path.join(DATA, "picks_log.csv")
LOG_FIELDS = ["date", "league", "event_id", "matchup", "pick", "side",
              "model_prob", "edge", "ml", "units", "result", "profit"]


def load_elo(key):
    path = os.path.join(DATA, f"elo_{key}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_elo(key, ratings):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, f"elo_{key}.json"), "w") as f:
        json.dump(ratings, f)


def seen_event(key, event_id):
    """Track processed finals so Elo never double-updates."""
    os.makedirs(DATA, exist_ok=True) 
    path = os.path.join(DATA, f"seen_{key}.json")
    seen = set()
    if os.path.exists(path):
        with open(path) as f:
            seen = set(json.load(f))
    hit = event_id in seen
    if not hit:
        seen.add(event_id)
        keep = list(seen)[-3000:]
        with open(path, "w") as f:
            json.dump(keep, f)
    return hit


def read_log():
    if not os.path.exists(LOG):
        return []
    with open(LOG) as f:
        return list(csv.DictReader(f))


def write_log(rows):
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_pick(row):
    rows = read_log()
    rows.append(row)
    write_log(rows)


def grade_pending(finals_by_event):
    """finals_by_event: {event_id: 'home'|'away'} winners. Returns (wins, losses)."""
    rows = read_log()
    w = l = 0
    for r in rows:
        if r["result"]:
            continue
        winner = finals_by_event.get(r["event_id"])
        if winner is None:
            continue
        units = float(r["units"] or 1)
        if winner == r["side"]:
            r["result"] = "W"
            ml = float(r["ml"])
            r["profit"] = round(units * (ml / 100.0 if ml > 0 else 100.0 / -ml), 3)
            w += 1
        else:
            r["result"] = "L"
            r["profit"] = -units
            l += 1
    write_log(rows)
    return w, l


def record_summary():
    rows = read_log()
    w = sum(1 for r in rows if r["result"] == "W")
    l = sum(1 for r in rows if r["result"] == "L")
    units = sum(float(r["profit"] or 0) for r in rows if r["result"])
    risked = sum(float(r["units"] or 1) for r in rows if r["result"])
    roi = (units / risked * 100) if risked else 0.0
    return {"w": w, "l": l, "units": round(units, 2), "roi": round(roi, 1)}
