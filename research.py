"""Web research on a matchup. Two modes (research.mode in config.yaml):

  headlines (default, free, no key): Google News RSS for both sides, last N days,
      injury / lineup / availability keywords first. Shown under the pick as 📰
      lines; strong keywords about the pick ("ruled out", "withdraws"...) become
      🚩 flags. No probability nudge.
  claude (paid): Claude + the web_search tool returns a structured brief
      (health, form, situational, red flags, lean). The lean becomes a small,
      capped Elo nudge on the pick and a red flag demotes an EDGE to an unstaked
      LEAN. Needs ANTHROPIC_API_KEY. Hard per-run cap for cost control.

Both modes cache per matchup per day (data/v2/research/<date>.json), log to
picks_log.csv, and degrade to "no research" on any failure so the card still goes out.
"""
import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

DEFAULTS = {
    "mode": "headlines",          # headlines (free) | claude (paid) | off
    "max_per_run": 60,            # matchups researched per run (set ~8 in claude mode)
    "near_edge": 0.02,            # claude mode: also research leans within this of edge_threshold
    # headlines mode
    "headlines_days": 7,
    "headlines_max": 3,           # lines shown per matchup
    "headlines_demote": False,    # let a strong headline about the pick demote an EDGE
    # claude mode
    "model": "claude-haiku-4-5",
    "effort": "low",              # low | medium | high — depth vs cost/latency
    "max_searches": 3,            # web searches Claude may run per matchup
    "elo_per_point": 8,           # research lean points (-3..+3) -> Elo on the pick (max ±24)
    "demote_on_red_flag": True,   # a red flag turns an EDGE into an unstaked LEAN
}

# words that make a headline worth showing / flagging (only counted when the
# headline actually names the competitor, see _mentions)
WATCH = re.compile(r"\b(injur\w+|ruled out|out for|out of the|sits? out|sidelined|doubtful|questionable|"
                   r"will miss|misses|missing|lineup|starting lineup|starter|scratch\w*|withdraw\w*|"
                   r"retire[sd]?\b|pulls? out|suspend\w*|banned|illness|concussion|surgery|"
                   r"\bIL\b|injured list|day-to-day|benched|fatigue|fitness|return[s]? (from|to))", re.I)
STRONG = re.compile(r"\b(ruled out|out for (the )?(season|year|match|game|week)|will miss|withdraws?|withdrawn|"
                    r"pulls? out|retires?\b|suspended|placed on (the )?(10|15|60)-day IL|season-ending|scratched)\b", re.I)
# betting previews / listings: shown only if they also carry an injury or lineup word
NOISE = re.compile(r"\b(prediction|predictions|odds|betting|best bets?|picks?|h2h|head-to-head|preview|"
                   r"how to watch|live stream\w*|tips|highlights|welcomes|things you didn'?t know)\b", re.I)
STOP = {"city", "united", "town", "club", "state", "real", "athletic", "sporting", "saint", "north", "south", "east", "west"}

_client = None
_state = {"used": 0, "date": None, "cache": {}, "note": ""}
_cfg = dict(DEFAULTS)


def configure(cfg):
    _cfg.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS})


def _cache_path(data_dir, date):
    d = os.path.join(data_dir, "research")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{date}.json")


def _load_cache(data_dir, date):
    if _state["date"] != date:
        _state["date"], _state["cache"] = date, {}
        p = _cache_path(data_dir, date)
        if os.path.exists(p):
            try:
                with open(p) as f:
                    _state["cache"] = json.load(f)
            except (OSError, ValueError):
                _state["cache"] = {}
    return _state["cache"]


def _save_cache(data_dir, date):
    with open(_cache_path(data_dir, date), "w") as f:
        json.dump(_state["cache"], f, indent=1)


def mode():
    m = str(_cfg.get("mode", "headlines")).lower()
    if _cfg.get("enabled") is False:
        m = "off"
    return m


def available():
    """Why research is off (str) or '' if it can run."""
    m = mode()
    if m == "off":
        return "research disabled in config"
    if m == "claude":
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return "no ANTHROPIC_API_KEY secret (set research.mode: headlines for the free option)"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "anthropic package not installed"
    return ""


def eligible(tier, edge, threshold):
    """Headlines are free: research every posted pick. Claude costs: edges and near-edges only."""
    if mode() == "headlines":
        return True
    return tier == "EDGE" or edge >= threshold - float(_cfg["near_edge"])


# ------------------------------------------------------------- headlines mode
def _fetch_rss(query, days):
    """Google News RSS search -> list of {title, source, link, when(datetime)}."""
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": f"{query} when:{int(days)}d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                     headers={"User-Agent": "Mozilla/5.0 (EdgeBot)"}, timeout=15)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        out.append({"title": title, "source": src, "link": (it.findtext("link") or "").strip(), "when": when})
    return out


def _display_name(name):
    try:
        from espn import ALIASES
        return ALIASES.get(name.lower().strip(), name).title() if name.lower().strip() in ALIASES else name
    except ImportError:
        return name


def _keys(name, alias=""):
    """Tokens that identify a competitor in a headline: 'Carlos Alcaraz' -> {alcaraz};
    'Philadelphia' + ESPN alias 'Philadelphia Eagles' -> {philadelphia, eagles}."""
    toks = set()
    for n in (name, alias, _display_name(name)):
        if not n:
            continue
        words = re.findall(r"[a-z0-9]+", n.lower())
        toks.update(w for w in words if len(w) >= 4 and w not in STOP)
        if len(words) >= 2 and len(words[-1]) >= 3:
            toks.add(words[-1])                      # surname / nickname
    if not toks:
        toks.add(name.lower())
    return toks


def _mentions(keys, title):
    t = title.lower()
    return any(k in t for k in keys)


def _headlines(date, league_label, matchup, pick, opp, sport_hint, aliases=None):
    days, n = int(_cfg["headlines_days"]), int(_cfg["headlines_max"])
    aliases = aliases or {}
    picked, flags, health = [], [], {"pick": [], "opp": []}
    for role, name in (("pick", pick), ("opp", opp)):
        full = aliases.get(name) or _display_name(name)
        q = f'"{full}"' if full != name else f'"{name}" {league_label}'
        try:
            items = _fetch_rss(q, days)
        except Exception as e:
            print(f"[research] rss {name}: {type(e).__name__}: {str(e)[:80]}")
            continue
        keys = _keys(name, aliases.get(name, ""))
        seen_titles = set()
        kept = []
        for i in items:
            t = i["title"].lower().strip()
            if t in seen_titles or not _mentions(keys, i["title"]):   # dupes / not actually about them
                continue
            if NOISE.search(i["title"]) and not WATCH.search(i["title"]):   # preview spam
                continue
            seen_titles.add(t)
            kept.append(i)
        items = kept
        items.sort(key=lambda i: i["when"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        hits = [i for i in items if WATCH.search(i["title"])]
        for i in hits[:2]:
            health[role].append(i["title"])
            if role == "pick" and STRONG.search(i["title"]):
                flags.append(i["title"])
        for i in (hits + [i for i in items if i not in hits])[:n]:
            picked.append({"side": name, "title": i["title"], "source": i["source"],
                           "date": i["when"].strftime("%b %d") if i["when"] else "",
                           "watch": bool(WATCH.search(i["title"]))})
    if not picked:
        return None
    picked.sort(key=lambda h: (not h["watch"], h["date"]), reverse=False)
    return {
        "mode": "headlines",
        "summary": f"{sum(1 for h in picked if h['watch'])} injury/lineup headline(s) in last {days}d",
        "pick_health": "; ".join(health["pick"]) or "no issues found",
        "opp_health": "; ".join(health["opp"]) or "no issues found",
        "recent_form": "", "situational": "nothing notable",
        "red_flags": flags[:2], "lean": 0, "confidence": "low", "sources": [],
        "headlines": picked[: n * 2],
    }


# ---------------------------------------------------------------- claude mode


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=240.0)
    return _client


def _ask(prompt):
    """One research call. Handles pause_turn continuations and returns the parsed JSON."""
    import anthropic
    client = _get_client()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": int(_cfg["max_searches"])}]
    messages = [{"role": "user", "content": prompt}]
    common = dict(model=_cfg["model"], max_tokens=6000, system=SYSTEM, tools=tools,
                  output_config={"effort": _cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}})
    use_fallbacks = True
    for _ in range(4):                      # initial call + up to 3 pause_turn continuations
        if use_fallbacks:
            try:
                # Server-side refusal fallback: if the primary model declines, the API
                # re-runs the request on a fallback model inside the same call.
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks="default", messages=messages, **common)
            except anthropic.BadRequestError:
                use_fallbacks = False       # beta not accepted here: plain request instead
                resp = client.messages.create(messages=messages, **common)
        else:
            resp = client.messages.create(messages=messages, **common)
        if resp.stop_reason == "pause_turn":
            messages = messages + [{"role": "assistant", "content": resp.content}]
            continue
        if resp.stop_reason == "refusal":
            raise RuntimeError("research request refused")
        texts = [b.text for b in resp.content if b.type == "text"]
        if not texts:
            raise RuntimeError(f"no text in response (stop_reason={resp.stop_reason})")
        return json.loads(texts[-1])
    raise RuntimeError("research did not finish (too many pause_turns)")


def lookup(data_dir, date, league_label, matchup, pick, opp, pick_price, notes, sport_hint="", aliases=None):
    """Research one matchup. Returns the brief dict (from cache when already done today)
    or None when research is unavailable, over budget, or failed."""
    if available():
        return None
    cache = _load_cache(data_dir, date)
    key = f"{league_label}|{matchup}|{pick}"
    if key in cache:
        return cache[key]
    if _state["used"] >= int(_cfg["max_per_run"]):
        _state["note"] = f"research cap {_cfg['max_per_run']}/run reached"
        return None
    _state["used"] += 1
    if mode() == "headlines":
        brief = _headlines(date, league_label, matchup, pick, opp, sport_hint, aliases)
        if brief:
            brief["_ts"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            cache[key] = brief
            _save_cache(data_dir, date)
        return brief
    prompt = (
        f"Date: {date}. League: {league_label}{(' (' + sport_hint + ')') if sport_hint else ''}.\n"
        f"Matchup: {matchup}\n"
        f"PICK (side the model likes): {pick}{(' (' + aliases[pick] + ')') if aliases and aliases.get(pick) else ''}  — Kalshi YES price {int(round((pick_price or 0) * 100))}¢\n"
        f"OPPONENT: {opp}{(' (' + aliases[opp] + ')') if aliases and aliases.get(opp) else ''}\n"
        f"Model notes: {'; '.join(notes) if notes else 'none'}\n\n"
        "Research both sides and fill the JSON."
    )
    try:
        brief = _ask(prompt)
    except Exception as e:                 # never let research break the run
        print(f"[research] {matchup}: {type(e).__name__}: {str(e)[:160]}")
        return None
    brief["mode"] = "claude"
    brief["lean"] = max(-3, min(3, int(brief.get("lean", 0) or 0)))
    brief["red_flags"] = [str(x) for x in (brief.get("red_flags") or [])][:4]
    brief["_ts"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    cache[key] = brief
    _save_cache(data_dir, date)
    return brief


def elo_adjust(brief):
    """Elo points to add to the PICK from the research lean (capped by config)."""
    if not brief:
        return 0.0
    return float(brief["lean"]) * float(_cfg["elo_per_point"])


def red_flag(brief):
    """Should this brief stop an EDGE from being staked?"""
    if not brief or not brief.get("red_flags"):
        return False
    if brief.get("mode") == "headlines":
        return bool(_cfg.get("headlines_demote", False))
    return bool(_cfg.get("demote_on_red_flag", True))


def card_lines(brief):
    """Lines to show under the pick on the Discord card."""
    if not brief:
        return []
    if brief.get("mode") == "headlines":
        out = [f"📰 {h['side']} · {h['date']} · {h['title']}" + (f" ({h['source']})" if h["source"] else "")
               for h in brief.get("headlines", [])[: int(_cfg["headlines_max"])]]
        if brief.get("red_flags"):
            out.append("🚩 " + "; ".join(brief["red_flags"]))
        return out
    out = [f"🔎 {brief['summary']} _(lean {brief['lean']:+d}, {brief['confidence']} conf)_"]
    health = []
    if brief.get("pick_health") and "no issues" not in brief["pick_health"].lower():
        health.append(brief["pick_health"])
    if brief.get("opp_health") and "no issues" not in brief["opp_health"].lower():
        health.append(brief["opp_health"])
    if health:
        out.append("🏥 " + " | ".join(health))
    if brief.get("situational") and "nothing notable" not in brief["situational"].lower():
        out.append("📋 " + brief["situational"])
    if brief.get("red_flags"):
        out.append("🚩 " + "; ".join(brief["red_flags"]))
    return out


def run_note():
    return _state["note"]


def label():
    m = mode()
    return "headlines (Google News, free)" if m == "headlines" else f"claude ({_cfg['model']})"
