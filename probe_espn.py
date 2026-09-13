"""Round 2: find an ESPN endpoint that actually returns injuries.

Round 1 established that teams/{id}?enable=injuries returns nothing for NFL or
college football - team_out_count has silently returned 0 for every pick ever
logged. Try the alternatives and print what each one gives.
"""
import json
import espn

def head(t): print("\n" + "=" * 8 + " " + t)

def get(url, **params):
    try:
        r = espn.S.get(url, params=params or None, timeout=25)
        return r.status_code, (r.json() if r.headers.get("content-type","").startswith("application/json") else None)
    except Exception as e:
        return type(e).__name__, None

for sport, league, date, grp in (("football","nfl","20260913",None),
                                 ("football","college-football","20260912",80)):
    head(f"{league}: find a live event")
    p = {"dates": date, "limit": 100}
    if grp: p["groups"] = grp
    st, js = get(f"{espn.BASE}/{sport}/{league}/scoreboard", **p)
    evs = (js or {}).get("events", [])
    print(f"  scoreboard {st}, {len(evs)} events")
    if not evs: continue
    ev = evs[0]; eid = ev.get("id")
    comp = (ev.get("competitions") or [{}])[0]
    tid = ((comp.get("competitors") or [{}])[0].get("team") or {}).get("id")
    print(f"  event {eid} ({ev.get('shortName')}), team id {tid}")

    head(f"{league}: summary?event= (per-game, both teams in one call)")
    st, js = get(f"{espn.BASE}/{sport}/{league}/summary", event=eid)
    print("  status:", st, "| top keys:", list((js or {}).keys())[:18])
    inj = (js or {}).get("injuries")
    print(f"  injuries key present: {inj is not None}, entries: {len(inj) if inj else 0}")
    if inj:
        for team_block in inj[:2]:
            print("   team block keys:", list(team_block.keys()))
            t = team_block.get("team") or {}
            print("   team:", t.get("displayName") or t.get("abbreviation"), "| id", t.get("id"))
            for rec in (team_block.get("injuries") or [])[:3]:
                print("   record:", json.dumps(rec, indent=1)[:700])

    head(f"{league}: teams/{{id}}/injuries")
    st, js = get(f"{espn.BASE}/{sport}/{league}/teams/{tid}/injuries")
    print("  status:", st, "| keys:", list((js or {}).keys())[:12] if js else None)
    if js: print("  ", json.dumps(js, indent=1)[:500])

    head(f"{league}: core API team injuries")
    st, js = get(f"https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{league}/teams/{tid}/injuries",
                 limit=50)
    print("  status:", st, "| keys:", list((js or {}).keys())[:12] if js else None)
    if js and js.get("items"):
        print(f"   {js.get('count')} items; first ref: {js['items'][0]}")
        st2, one = get(js["items"][0].get("$ref","").replace("http://","https://"))
        print("   dereferenced:", st2)
        if one: print("   ", json.dumps(one, indent=1)[:800])
