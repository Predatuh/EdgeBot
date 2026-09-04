"""Web research on a matchup via the Claude API + web search.

For each candidate pick, Claude searches the web for what the Kalshi price might
be missing: recent results, injuries / lineups / starting pitcher / QB status,
suspensions, travel & schedule, motivation (must-win, resting starters), and
returns a structured brief. The brief is:
  - shown under the pick on the Discord card,
  - logged to picks_log.csv (summary, lean, adj, flag) and data/v2/research/<date>.json,
  - turned into a small, capped Elo nudge on the pick and a red-flag demotion
    (EDGE -> LEAN) so a known problem never gets staked.

Runs only when ANTHROPIC_API_KEY is set and research.enabled is true; every
failure degrades to "no research" so the card still goes out.
"""
import datetime as dt
import json
import os

DEFAULTS = {
    "enabled": True,
    "model": "claude-opus-5",
    "effort": "medium",           # low | medium | high — research depth vs cost/latency
    "max_searches": 6,            # web searches Claude may run per matchup
    "max_per_run": 15,            # hard cap on matchups researched per run (cost control)
    "near_edge": 0.02,            # also research leans within this of edge_threshold
    "elo_per_point": 8,           # research lean points (-3..+3) -> Elo on the pick (max ±24)
    "demote_on_red_flag": True,   # a red flag turns an EDGE into an unstaked LEAN
}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "<= 2 sentences: what matters for this matchup that the price may not fully reflect."},
        "pick_health": {"type": "string", "description": "Injuries / lineup / availability news for the PICK side, or 'no issues found'."},
        "opp_health": {"type": "string", "description": "Same for the OPPONENT."},
        "recent_form": {"type": "string", "description": "One line on each side's last few results and level of competition."},
        "situational": {"type": "string", "description": "Schedule, travel, rest, motivation, weather, surface, starting pitcher/QB, or 'nothing notable'."},
        "red_flags": {"type": "array", "items": {"type": "string"},
                      "description": "Concrete reasons NOT to stake the pick (key player out, resting starters, lineup unknown, match in doubt). Empty if none."},
        "lean": {"type": "integer", "minimum": -3, "maximum": 3,
                 "description": "How the research moves you on the PICK: -3 strongly against ... 0 neutral ... +3 strongly for. Use 0 unless something concrete and recent was found."},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"], "description": "How much recent, specific information was actually found."},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "Up to 4 source URLs used."},
    },
    "required": ["summary", "pick_health", "opp_health", "recent_form", "situational", "red_flags", "lean", "confidence", "sources"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a sports betting research analyst. You are given one matchup that trades on Kalshi, the "
    "side a quantitative model likes, and the model's own notes (Elo, form, H2H, injuries count, weather). "
    "Use web search to find current, specific information from the last ~10 days: injuries, lineups, "
    "starting pitcher / quarterback / key-player availability, suspensions, resting of starters, travel "
    "and schedule congestion, motivation (relegation, playoffs, dead rubber, tournament round), and for "
    "tennis: surface record, retirements, fatigue, recent withdrawals. Prefer official team/tour sites, "
    "major sports outlets and injury reports. Be skeptical of generic preview articles. Report facts with "
    "dates; if you could not confirm something, say so rather than guessing. The 'lean' must be 0 unless "
    "you found something concrete the market is unlikely to have fully priced. Return only the JSON."
)

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


def available():
    """Why research is off (str) or '' if it can run."""
    if not _cfg.get("enabled", True):
        return "research disabled in config"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "no ANTHROPIC_API_KEY secret"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "anthropic package not installed"
    return ""


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


def lookup(data_dir, date, league_label, matchup, pick, opp, pick_price, notes, sport_hint=""):
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
    prompt = (
        f"Date: {date}. League: {league_label}{(' (' + sport_hint + ')') if sport_hint else ''}.\n"
        f"Matchup: {matchup}\n"
        f"PICK (side the model likes): {pick}  — Kalshi YES price {int(round((pick_price or 0) * 100))}¢\n"
        f"OPPONENT: {opp}\n"
        f"Model notes: {'; '.join(notes) if notes else 'none'}\n\n"
        "Research both sides and fill the JSON."
    )
    try:
        brief = _ask(prompt)
    except Exception as e:                 # never let research break the run
        print(f"[research] {matchup}: {type(e).__name__}: {str(e)[:160]}")
        return None
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
    return bool(brief and brief.get("red_flags") and _cfg.get("demote_on_red_flag", True))


def card_lines(brief):
    """Lines to show under the pick on the Discord card."""
    if not brief:
        return []
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
