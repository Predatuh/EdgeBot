"""Union the accumulate-only state with whatever is on the remote right now.

picks_log.csv, tipsters_log.csv and the daily research caches only ever GAIN
entries, so when two runs finish at the same time the second must ADD the first's,
not replace them. The commit step in every workflow resolves conflicts with
`-X theirs` (correct for elo/seen/hist/stats, which each run regenerates in full);
without this merge the same option silently drops entries from the accumulating ones.

Entries already present locally win, since this run may have just graded or
refreshed them.
"""
import csv
import glob
import json
import os
import subprocess
import sys

import state


def _remote(path, ref="origin/main"):
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def _union(name, rel_path, read, write, fields, key):
    remote = _remote(rel_path)
    if not remote:
        return 0
    local = read()
    have = {key(r) for r in local}
    extra = [r for r in remote if key(r) not in have]
    if not extra:
        return 0
    for r in extra:                       # normalise to the current column set
        for f in fields:
            r.setdefault(f, "")
    write(local + extra)
    print(f"[merge] {name}: recovered {len(extra)} row(s) that were only on the remote")
    return len(extra)


def _union_cache(rel_path):
    """Research caches are dicts keyed by league|matchup|pick. Two workflows on the
    same day research different matchups, so whoever pushed last used to wipe the
    other's work - free to redo, but only because the headlines are free."""
    r = subprocess.run(["git", "show", f"origin/main:{rel_path}"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    try:
        remote = json.loads(r.stdout)
        with open(rel_path) as f:
            local = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(remote, dict) or not isinstance(local, dict):
        return 0
    extra = {k: v for k, v in remote.items() if k not in local}
    if not extra:
        return 0
    local.update(extra)
    with open(rel_path, "w") as f:
        json.dump(local, f, indent=1)
    print(f"[merge] {rel_path}: recovered {len(extra)} brief(s) that were only on the remote")
    return len(extra)


def main():
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
    n = _union("picks_log", "data/v2/picks_log.csv", state.read_log, state.write_log,
               state.LOG_FIELDS, lambda r: r.get("event_id", ""))
    n += _union("tipsters_log", "data/v2/tipsters_log.csv", state.read_tips, state.write_tips,
                state.TIP_FIELDS, lambda r: (r.get("date", ""), r.get("tipster", ""), r.get("event_id", "")))
    for path in sorted(glob.glob(os.path.join("data", "v2", "research", "*.json"))):
        n += _union_cache(path)
    if not n:
        print("[merge] nothing to recover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
