"""Kalshi public API (no key needed for market data).
- open_events(series): today's bettable events with live YES prices per side
- settled_events(series): historical results (who won) for building ratings

Kalshi is the market we bet on, so its prices are the market we beat."""
import re
import requests

B = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _page(params):
    out, cur = [], None
    while True:
        p = dict(params)
        if cur:
            p["cursor"] = cur
        r = S.get(B + "/markets", params=p, timeout=30)
        r.raise_for_status()
        js = r.json()
        out += js.get("markets", [])
        cur = js.get("cursor")
        if not cur:
            break
    return out


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def event_date(ticker):
    """KXATPMATCH-26SEP04YIBALC -> '2026-09-04'"""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker)
    if not m:
        return ""
    return f"20{m.group(1)}-{MONTHS.get(m.group(2), 1):02d}-{int(m.group(3)):02d}"


def _code(market_ticker):
    return market_ticker.rsplit("-", 1)[-1]


def _price(m):
    """Return (mid_prob, ask_price). Kalshi prices are already probabilities."""
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    last = _f(m.get("last_price_dollars"))
    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2.0, ask
    if last:
        return last, last
    return None, None


def open_events(series):
    ms = _page({"series_ticker": series, "status": "open", "limit": 1000})
    ev = {}
    for m in ms:
        et = m["event_ticker"]
        e = ev.setdefault(et, {
            "event": et, "series": series, "date": event_date(et),
            "close": m.get("expected_expiration_time") or m.get("close_time"),
            "sides": [],
        })
        mid, ask = _price(m)
        code = _code(m["ticker"])
        e["sides"].append({
            "name": m.get("yes_sub_title") or m.get("title", "?").replace(" wins", ""),
            "ticker": m["ticker"], "code": code,
            "prob": mid, "ask": ask,
            "vol": _f(m.get("volume_fp")) or 0.0,
            "is_tie": code == "TIE" or (m.get("yes_sub_title") or "").lower() == "tie",
            # Kalshi event tickers end with the HOME team's code (AWAY+HOME)
            "home": (not code == "TIE") and et.endswith(code),
        })
    return list(ev.values())


def settled_events(series):
    ms = _page({"series_ticker": series, "status": "settled", "limit": 1000})
    ev = {}
    for m in ms:
        et = m["event_ticker"]
        e = ev.setdefault(et, {"event": et, "date": event_date(et), "sides": []})
        code = _code(m["ticker"])
        e["sides"].append({
            "name": m.get("yes_sub_title") or "?",
            "won": m.get("result") == "yes",
            "is_tie": code == "TIE",
            "home": (not code == "TIE") and et.endswith(code),
        })
    return sorted(ev.values(), key=lambda e: e["date"])


def winner(ev):
    """Name of the winning side, 'Tie', or None if not settled cleanly."""
    for s in ev["sides"]:
        if s["won"]:
            return "Tie" if s["is_tie"] else s["name"]
    return None
