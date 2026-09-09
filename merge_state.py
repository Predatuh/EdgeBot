"""Union the append-only logs with whatever is on the remote right now.

picks_log.csv and tipsters_log.csv only ever gain rows, so when two runs finish
at the same time the second must ADD the first's rows, not replace them. The
commit step in every workflow resolves conflicts with `-X theirs` (correct for
elo/seen/hist/stats, which each run regenerates in full); without this merge the
same option silently drops rows from an append-only log.

Rows already present locally win, since this run may have just graded them.
"""
import csv
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


def main():
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
    n = _union("picks_log", "data/v2/picks_log.csv", state.read_log, state.write_log,
               state.LOG_FIELDS, lambda r: r.get("event_id", ""))
    n += _union("tipsters_log", "data/v2/tipsters_log.csv", state.read_tips, state.write_tips,
                state.TIP_FIELDS, lambda r: (r.get("date", ""), r.get("tipster", ""), r.get("event_id", "")))
    if not n:
        print("[merge] nothing to recover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
