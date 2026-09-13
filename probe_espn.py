"""One-off: dump the ESPN payloads the next three changes depend on.

Guessing a response shape cost a whole cycle on the Kalshi candlesticks, so this
prints the real thing: whether college football carries injuries at all, what an
injury record actually contains (position? player? status?), and what a historical
scoreboard returns for a past date.
"""
import datetime as dt
import json

import espn

def head(t): print("\n" + "=" * 8 + " " + t)

# --- 1. is there a college football board at all, and what team ids does it give
head("NCAAF scoreboard for a past Saturday (2025-09-13)")
js = espn.S.get(f"{espn.BASE}/football/college-football/scoreboard",
                params={"dates": "20250913", "groups": 80, "limit": 400}, timeout=30).json()
evs = js.get("events", [])
print(f"events: {len(evs)}")
if evs:
    ev = evs[0]
    comp = (ev.get("competitions") or [{}])[0]
    print("event keys:", list(ev.keys()))
    print("competition keys:", list(comp.keys()))
    for c in comp.get("competitors", []):
        t = c.get("team") or {}
        print(f"   {c.get('homeAway'):<5} id={t.get('id'):<6} score={c.get('score')!r:>6} "
              f"winner={c.get('winner')!r:<6} displayName={t.get('displayName')!r}")
    print("status:", json.dumps((comp.get("status") or {}).get("type", {}), indent=1)[:300])
    print("date:", ev.get("date"))

# --- 2. does a college team carry injuries, and what is in a record
head("NCAAF team injuries payload")
tid = None
for c in ((evs[0].get("competitions") or [{}])[0].get("competitors") or []):
    tid = (c.get("team") or {}).get("id"); break
print("team id:", tid)
if tid:
    r = espn.S.get(f"{espn.BASE}/football/college-football/teams/{tid}",
                   params={"enable": "injuries"}, timeout=25).json()
    team = r.get("team") or {}
    print("team keys:", list(team.keys()))
    inj = team.get("injuries") or []
    print(f"injuries returned: {len(inj)}")
    if inj:
        print("first record:"); print(json.dumps(inj[0], indent=1)[:1200])

head("NFL team injuries payload (known to work) for comparison")
nb = espn.S.get(f"{espn.BASE}/football/nfl/scoreboard", params={"limit": 20}, timeout=25).json()
nid = None
for e in nb.get("events", []):
    for c in ((e.get("competitions") or [{}])[0].get("competitors") or []):
        nid = (c.get("team") or {}).get("id"); break
    if nid: break
print("team id:", nid)
if nid:
    r = espn.S.get(f"{espn.BASE}/football/nfl/teams/{nid}",
                   params={"enable": "injuries"}, timeout=25).json()
    inj = ((r.get("team") or {}).get("injuries")) or []
    print(f"injuries returned: {len(inj)}")
    for rec in inj[:3]:
        print(json.dumps(rec, indent=1)[:900])

# --- 3. how far back does the scoreboard go, and can we walk a season
head("how much history is reachable")
for d in ("20240914", "20230916"):
    try:
        j = espn.S.get(f"{espn.BASE}/football/college-football/scoreboard",
                       params={"dates": d, "groups": 80, "limit": 400}, timeout=30).json()
        n = len(j.get("events", []))
        done = sum(1 for e in j.get("events", [])
                   if ((e.get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("completed"))
        print(f"   {d}: {n} events, {done} completed")
    except Exception as e:
        print(f"   {d}: {type(e).__name__} {str(e)[:80]}")

head("NFL scoreboard, past date")
j = espn.S.get(f"{espn.BASE}/football/nfl/scoreboard", params={"dates": "20250914", "limit": 100}, timeout=30).json()
print(f"events: {len(j.get('events', []))}")
for e in j.get("events", [])[:2]:
    comp = (e.get("competitions") or [{}])[0]
    print("  ", e.get("date"), [(c.get("homeAway"), (c.get("team") or {}).get("displayName"), c.get("score"))
                                 for c in comp.get("competitors", [])])
