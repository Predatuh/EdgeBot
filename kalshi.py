"""Kalshi public API (no key needed for market data).
- open_events(series): today's bettable events with live YES prices per side
- settled_events(series, since_ts): historical results (who won) for building
  ratings, plus each side's last traded price (the closing line, for CLV)

Kalshi is the market we bet on, so its prices are the market we beat."""
import re
import time

import requests

B = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "EdgeBot/2"})
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

# A YES bid/ask gap wider than this means there's no real market to beat.
# An empty book shows up as bid 0 / ask 1, so it fails this test too. main.py
# overrides it from config.yaml (max_spread).
MAX_SPREAD = 0.15


class KalshiError(Exception):
    pass


def _get(path, params, tries=4):
    """GET with retry/backoff on rate limits, 5xx and network hiccups."""
    last = None
    for i in range(tries):
        try:
            r = S.get(B + path, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise KalshiError(f"HTTP {r.status_code}: {r.text[:120]}")
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            raise                       # other 4xx: retrying won't help
        except (KalshiError, requests.RequestException, ValueError) as e:
            last = e
            time.sleep(1.5 * 2 ** i)
    raise KalshiError(f"giving up on {path}: {last}")


def _page(params):
    out, cur = [], None
    while True:
        p = dict(params)
        if cur:
            p["cursor"] = cur
        js = _get("/markets", p)
        out += js.get("markets", [])
        cur = js.get("cursor")
        if not cur:
            break
    return out


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None        # NaN guard


def _money(m, key):
    """A price as a probability. Prefers the *_dollars string fields, falls
    back to the legacy integer-cent fields."""
    v = _f(m.get(key + "_dollars"))
    if v is None:
        c = _f(m.get(key))
        v = c / 100.0 if c is not None else None
    return v


def event_date(ticker, fallback_iso=""):
    """KXATPMATCH-26SEP04YIBALC -> '2026-09-04'"""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker or "")
    if m and m.group(2) in MONTHS:
        return f"20{m.group(1)}-{MONTHS[m.group(2)]:02d}-{int(m.group(3)):02d}"
    return (fallback_iso or "")[:10]


def _code(market_ticker):
    return market_ticker.rsplit("-", 1)[-1]


REG_PREFIX = re.compile(r"^(reg(ular)?\.?\s*time|regulation|90\s*min(ute)?s?)\s*:\s*", re.I)


def _name(m):
    """Side name. Cup/qualifier events carry both 'X' (to advance) and
    'Reg Time: X' (to win in regulation) markets; returns (clean_name, is_reg_time)."""
    n = (m.get("yes_sub_title") or "").strip()
    if not n:
        n = re.sub(r"\s+wins?\??$", "", (m.get("title") or "?").strip())
    reg = bool(REG_PREFIX.match(n))
    return REG_PREFIX.sub("", n).strip(), reg


def _is_tie(code, name):
    return code == "TIE" or name.lower() in ("tie", "draw")


def _price(m, max_spread=None):
    """Return (mid_prob, ask_price), or (None, None) when there's no usable market."""
    cap = MAX_SPREAD if max_spread is None else max_spread
    bid, ask = _money(m, "yes_bid"), _money(m, "yes_ask")
    if bid is not None and ask is not None:
        if not (0 < ask < 1) or ask - bid > cap:
            return None, None
        return (bid + ask) / 2.0, ask
    last = _money(m, "last_price")          # very old API shape: no book fields at all
    if last and 0 < last < 1:
        return last, last
    return None, None


def _mark_home(sides, event_ticker):
    """Kalshi event tickers end with the HOME team's code (AWAY+HOME).
    Exactly one side should match; if codes overlap take the longest match,
    and if nothing matches fall back to listing order so sorting stays stable."""
    for s in sides:
        s["home"] = False
    teams = [s for s in sides if not s["is_tie"]]
    hits = [s for s in teams if s["code"] and event_ticker.endswith(s["code"])]
    home = max(hits, key=lambda s: len(s["code"])) if hits else (teams[-1] if teams else None)
    if home:
        for s in teams:                 # both the 'advance' and 'Reg Time' markets of that team
            s["home"] = s["name"] == home["name"]


def open_events(series, max_spread=None):
    ms = _page({"series_ticker": series, "status": "open", "limit": 1000})
    ev = {}
    for m in ms:
        et = m.get("event_ticker") or ""
        close = m.get("expected_expiration_time") or m.get("close_time") or ""
        e = ev.setdefault(et, {"event": et, "series": series,
                               "date": event_date(et, close), "close": close, "sides": []})
        mid, ask = _price(m, max_spread)
        code, (name, reg) = _code(m["ticker"]), _name(m)
        e["sides"].append({
            "name": name, "ticker": m["ticker"], "code": code, "reg": reg,
            "prob": mid, "ask": ask, "bid": _money(m, "yes_bid"),
            "vol": _f(m.get("volume_fp")) or _f(m.get("volume")) or 0.0,
            "oi": _f(m.get("open_interest_fp")) or _f(m.get("open_interest")) or 0.0,
            "is_tie": _is_tie(code, name),
        })
    for e in ev.values():
        _mark_home(e["sides"], e["event"])
    return list(ev.values())


def settled_events(series, since_ts=None):
    """Settled markets grouped by event, oldest first. since_ts (unix seconds)
    limits the pull to markets that closed after that time so daily runs only
    fetch what's new; any error there falls back to the full history."""
    params = {"series_ticker": series, "status": "settled", "limit": 1000}
    if since_ts:
        try:
            ms = _page(dict(params, min_close_ts=int(since_ts)))
        except Exception as e:
            print(f"[kalshi] {series} incremental fetch failed ({e}); pulling full history")
            ms = _page(params)
    else:
        ms = _page(params)
    ev = {}
    for m in ms:
        et = m.get("event_ticker") or ""
        close = m.get("close_time") or m.get("expiration_time") or ""
        e = ev.setdefault(et, {"event": et, "date": event_date(et, close), "close": close, "sides": []})
        code, (name, reg) = _code(m["ticker"]), _name(m)
        e["sides"].append({
            "name": name, "code": code, "reg": reg,
            "won": m.get("result") == "yes",
            "last": _money(m, "last_price"),        # closing line, used for CLV
            "is_tie": _is_tie(code, name),
        })
    for e in ev.values():
        _mark_home(e["sides"], e["event"])
    return sorted(ev.values(), key=lambda e: (e["date"], e["close"]))


def match_sides(ev):
    """The sides that describe the match result. When an event lists both
    'advance' and 'regulation time' markets, use the regulation ones (they are
    what Elo models and what the Tie market belongs to)."""
    sides = ev["sides"]
    if any(s.get("reg") for s in sides):
        return [s for s in sides if s.get("reg") or s["is_tie"]]
    return sides


def winner(ev):
    """Name of the winning side, 'Tie', or None if not settled cleanly (void etc)."""
    for s in match_sides(ev):
        if s["won"]:
            return "Tie" if s["is_tie"] else s["name"]
    return None


def closing_probs(ev):
    """{side_name: last traded YES price} for a settled event."""
    return {("Tie" if s["is_tie"] else s["name"]): s["last"]
            for s in match_sides(ev) if s.get("last") is not None}
