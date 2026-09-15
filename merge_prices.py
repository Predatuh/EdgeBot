"""Keep research/notes/elo from the previous parlay.json when a prices-only run rewrites it."""
from __future__ import annotations

import json
import sys

KEEP = ("notes", "research", "flags", "elo_pick", "elo_opp", "emoji", "tier", "tier_label")


def _empty(v) -> bool:
    return v in (None, "", [], {})


def merge(old: dict, new: dict) -> dict:
    prev = {leg.get("ticker"): leg for leg in old.get("legs") or [] if leg.get("ticker")}
    for leg in new.get("legs") or []:
        prior = prev.get(leg.get("ticker"))
        if not prior:
            continue
        for key in KEEP:
            if not _empty(prior.get(key)) and _empty(leg.get(key)):
                leg[key] = prior[key]
    return new


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: merge_prices.py OLD.json NEW.json [OUT.json]", file=sys.stderr)
        return 2
    old_path, new_path = args[0], args[1]
    out_path = args[2] if len(args) > 2 else new_path
    try:
        with open(old_path, encoding="utf-8") as f:
            old = json.load(f)
    except FileNotFoundError:
        old = {}
    with open(new_path, encoding="utf-8") as f:
        new = json.load(f)
    merged = merge(old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {})
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, separators=(",", ":"))
    print(f"[prices] merged {len(merged.get('legs') or [])} legs -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
