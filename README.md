# EdgeBot v2 — Kalshi-first multi-sport picker

Runs on GitHub Actions twice a day (13:00 and 21:00 UTC). Kalshi is both the
market we bet into and the results source, so every sport Kalshi lists is
covered from one config: ATP/WTA tennis, NFL, college football, MLB, EPL,
La Liga, Ligue 1, Serie A, Bundesliga, MLS, Champions League, cricket (T20I,
ODI, Test, CPL). Turn a league on/off with `enabled:` in `config.yaml`.

## What one run does

1. **Ingest results** — pulls settled Kalshi markets per league (full history on
   the first run, incremental after that) into per-league Elo ratings and a
   compact game history (form, streak, head-to-head, rest days).
2. **Grade picks** — any logged pick whose event has settled gets W/L, units
   profit, and closing-line value (CLV) from the last traded Kalshi price.
3. **Model today's board** — for each open event: Elo + home advantage + rest
   nudge + injuries (players OUT via ESPN) + venue/weather, then blends the
   model probability with the de-vigged Kalshi price. The model's share
   (`market_weight`) shrinks further when either side has few rated games.
4. **Research the edges** — for every EDGE and near-edge pick, `research.py` asks
   Claude (with web search) for what the price may be missing: injuries, lineups,
   starting pitcher / QB, suspensions, rest and travel, motivation, surface and
   fatigue for tennis. The brief is shown under the pick (🔎 summary, 🏥 health,
   📋 situational, 🚩 red flags), logged, and turned into a capped Elo nudge on the
   pick (`elo_per_point` × lean, at most ±24 Elo). An EDGE with a red flag is
   posted as an unstaked LEAN. Needs the `ANTHROPIC_API_KEY` secret; without it the
   card just says research is off.
5. **Post the card** to Discord:
   - 🔥 **EDGE** — model beats the Kalshi ask by `edge_threshold`+, ask ≤ `max_price`,
     no extreme weather → staked (quarter-Kelly), tracked in units and ROI
   - 📌 **LEAN** — model's favorite with no real edge → paper pick, W-L only
   - ⏸️ **PASS** — only when there is no usable price (empty book or spread wider
     than `max_spread`); hidden unless `show_passes: true`
   - ⚠️ a line naming any league that errored, so a quiet card is never mistaken for a clean one
6. **Commit state** back to the repo (`data/v2/`).

Under each pick: form/streak, rest days, H2H, injuries OUT, venue, weather,
Elo ratings and the model weight used.

## Data (all in `data/v2/`)

| file | what |
|---|---|
| `picks_log.csv` | every pick, one row per event per day (first run of the day is the one tracked) |
| `stats.json` | record, units, ROI, avg edge, avg CLV, Brier (model vs market) — overall, last 7 days, per league; top-10 Elo per league |
| `elo_<league>.json` | current ratings (`_games` = games rated) |
| `hist_<league>.json` | game history used for form / H2H / rest |
| `seen_<league>.json` | settled events already ingested (dedupe) |
| `research/<date>.json` | full research briefs per matchup (also the per-day cache) |

`picks_log.csv` columns: `date, time_utc, league, event_id, matchup, pick, side,
tier, model_raw, model_prob, market_prob, edge, price, units, elo_pick, elo_opp,
conf, notes, research, research_lean, research_adj, research_flag, result, close_prob, clv, profit`.

- `model_raw` is the model before blending; `model_prob` is what the pick was made on.
- `clv = close_prob − price`: positive means we beat the closing line, the best
  early signal an edge is real before the W-L sample is big enough to mean anything.
- `by_research` in `stats.json` splits graded picks by how the research leaned
  (for / neutral / against / not researched / flagged). If "for_pick" doesn't beat
  "against_pick" over time, the research isn't earning its cost.
- `brier_model` vs `brier_market` in `stats.json`: if the model's score is not
  lower than the market's after a few hundred graded picks, it is not adding information.

## Tuning (`config.yaml`)

- `edge_threshold` 0.04 — raise for fewer, stronger EDGE plays
- `max_price` 0.90 — never buy YES above this
- `max_spread` 0.15 — bid/ask gap that counts as "no market"
- `market_weight` 0.5 / `full_conf_games` 25 — how much the model is trusted, and how many rated games each side needs for full trust
- `kelly_fraction` 0.25, `max_units` 1.0 — stake sizing
- `research:` block — model, effort, searches per matchup, per-run cap, `near_edge`, `elo_per_point`, `demote_on_red_flag`
- per league: `k` (Elo K), `home_adv`, `neutral` (tennis), `min_games` (drops the ⚠️low-data tag), `espn: [sport, league]` + `injuries` / `injury_elo` / `weather`

## Setup / ops

1. Add the repo secret `DISCORD_WEBHOOK_URL`, and `ANTHROPIC_API_KEY` if you want web research
   (roughly 15 researched matchups a day at medium effort; cached so the second daily run is free).
2. Actions → **Daily Picks** → **Run workflow**. The first run pulls Kalshi's full
   settled history for every enabled league, so allow a few minutes.
3. The workflow uses a concurrency group and `git pull --rebase -X theirs` before
   pushing, so two runs can't clobber each other's `data/` commit.

## Honest expectations

- Elo starts from Kalshi's history only, so leagues with a short Kalshi record show
  ⚠️low-data and lean heavily on the market until they've seen enough games.
- Win % means nothing; units, ROI and CLV in `data/v2/` are the truth.
- Kalshi lines are sharp-ish. Sustained positive CLV and 3–5% ROI is a very good result.
- ESPN and Open-Meteo are free public APIs; if one hiccups, the pick still goes
  out without that note.

## Layout

Flat: `main.py` (orchestration + model), `kalshi.py` (market + results),
`espn.py` (injuries/venue), `weather.py`, `elo.py`, `edge.py` (de-vig, Kelly),
`state.py` (persistence + analytics), `research.py` (Claude web research), `notify.py` (Discord). Adding a factor is
one function plus one adjustment line in `model_game`.
