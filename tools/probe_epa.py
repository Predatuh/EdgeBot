#!/usr/bin/env python3
"""Where free play-by-play with EPA actually lives, for NFL and college.

The dev container can reach GitHub but not collegefootballdata.com, so the
college half of this can only be answered from a runner.
"""
import gzip
import io
import urllib.request

UA = {"User-Agent": "EdgeBot/2"}


def head(url, note=""):
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            size = r.headers.get("Content-Length") or "?"
            print(f"  {r.status}  {int(size)/1e6:.1f}MB  {url.rsplit('/', 1)[-1]}  {note}"
                  if size.isdigit() else f"  {r.status}  {url.rsplit('/', 1)[-1]}  {note}")
            return True
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        print(f"  {code}  {url.rsplit('/', 1)[-1]}  {note}")
        return False


def peek_csv_gz(url, want, rows=0):
    """Stream the first chunk and report whether the columns we need are there."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read(3_000_000)
    except Exception as e:
        print("  peek failed:", type(e).__name__, str(e)[:120])
        return
    try:
        text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(2_000_000).decode("utf-8", "replace")
    except Exception as e:
        print("  gunzip failed:", type(e).__name__, str(e)[:120])
        return
    lines = text.splitlines()
    cols = lines[0].split(",") if lines else []
    print(f"  columns: {len(cols)} | rows in first chunk: {len(lines) - 1}")
    have = [c for c in want if c in cols]
    missing = [c for c in want if c not in cols]
    print(f"  have: {have}")
    print(f"  missing: {missing or 'nothing'}")


print("=== NFL: nflverse play-by-play (nflfastR's own EPA)")
NFL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_%d.csv.gz"
for yr in (2026, 2025):
    head(NFL % yr)
print("  checking columns in 2026:")
peek_csv_gz(NFL % 2026,
            ["posteam", "defteam", "epa", "success", "pass", "rush", "play_type",
             "week", "season", "wp", "home_team", "away_team"])

print("\n=== NFL: the smaller pre-aggregated team stats, if it exists")
for u in ("https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_2026.csv.gz",
          "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_reg_2026.csv.gz"):
    head(u)

print("\n=== College: cfbfastR-data, every layout I know of")
for u in (
    "https://github.com/sportsdataverse/cfbfastR-data/releases/download/pbp/play_by_play_2026.csv.gz",
    "https://github.com/sportsdataverse/cfbfastR-data/releases/download/cfb_pbp/play_by_play_2026.csv.gz",
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/pbp/csv/play_by_play_2026.csv.gz",
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/pbp/parquet/play_by_play_2026.parquet",
    "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/master/pbp/csv/play_by_play_2025.csv.gz",
    "https://github.com/sportsdataverse/cfbfastR-data/releases/download/pbp/play_by_play_2025.csv.gz",
):
    head(u)

print("\n=== College: collegefootballdata.com (blocked from the dev container)")
for u in ("https://api.collegefootballdata.com/teams?year=2026",
          "https://api.collegefootballdata.com/ppa/teams?year=2026",
          "https://api.collegefootballdata.com/stats/season/advanced?year=2026"):
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(400)
            print(f"  {r.status}  {u.split('?')[0].rsplit('/', 1)[-1]}  {body[:180]!r}")
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        body = b""
        try:
            body = e.read()[:200]
        except Exception:
            pass
        print(f"  {code}  {u.split('?')[0].rsplit('/', 1)[-1]}  {body!r}")
