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
4. **Research the picks** (`research.py`, two modes set by `research.mode`):
   - `headlines` (default, free, no key): Google News headlines from the last 7 days for
     both sides, injury / lineup / availability news first, shown as 📰 lines under the
     pick. A strong headline about the pick ("ruled out", "withdraws") shows as 🚩 and,
     if `headlines_demote: true`, unstakes the edge.
   - `claude` (paid, needs the `ANTHROPIC_API_KEY` secret): Claude with web search returns
     a structured brief (🔎 summary, 🏥 health, 📋 situational, 🚩 red flags) for EDGE and
     near-edge picks only. The lean becomes a capped Elo nudge on the pick (at most ±24 Elo)
     and a red flag posts the EDGE as an unstaked LEAN. Defaults to Haiku 4.5, 3 searches,
     `max_per_run: 8`, so a few dollars of credit lasts weeks.
5. **Post the card** to Discord:
   - 🔥 **EDGE** (staked) / 🧪 **PAPER EDGE** — the model beats the price you would
     actually pay by `edge_threshold`+ and clears every gate. **Staking is currently
     off**, so these post as paper and are still graded; see "Is it working?" below.
   - 📌 **market favourite** — the blended favourite, which is the market's favourite
     ~84% of the time. Tracked, but it does not test the model; `model_fav` (the side
     the raw Elo preferred) is the column that does.
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
| `tipsters_log.csv` | outside tipsters' slates: price, market prob, our pick, agreement, result, CLV, $100 P/L |

`picks_log.csv` columns: `date, time_utc, league, event_id, matchup, pick, side,
tier, model_raw, model_prob, market_prob, edge, price, units, elo_pick, elo_opp,
conf, notes, research, research_lean, research_adj, research_flag, result, close_prob, clv, profit`.

- `model_raw` is the model before blending; `model_prob` is what the pick was made on.
- `clv = close_prob − price`, where `close_prob` is the last **live** price seen
  before the event settled (`close_utc` records when). It is never taken from the
  settled market: Kalshi trades in-play, so a settled market's last trade is ~0.99 for
  the winner and ~0.01 for the loser — that is the result, not a close. Every CLV
  figure produced before 2026-09-10 was that mistake and has been cleared.
- `by_research` in `stats.json` splits graded picks by how the research leaned
  (for / neutral / against / not researched / flagged). If "for_pick" doesn't beat
  "against_pick" over time, the research isn't earning its cost.
- `brier_raw` vs `brier_market` is the real test — it scores the model's own opinion.
  `brier_model` scores the *blend*, which is mostly the market, so it is a near-tie by
  construction and tells you almost nothing.
- `model_vs_market` in `stats.json` splits every graded pick by how far the raw model
  disagreed with the price, and shows actual wins against market-implied wins in each
  bucket. This is the one table that can tell you the model has learned something.
- `by_gate` shows which staking gate stopped each would-be edge, so the paper filter
  stays measurable while staking is off.
- `brier_model` vs `brier_market` in `stats.json`: if the model's score is not
  lower than the market's after a few hundred graded picks, it is not adding information.

## Tuning (`config.yaml`)

- `edge_threshold` 0.04 — raise for fewer, stronger EDGE plays
- `max_price` 0.90 — never buy YES above this
- `max_spread` 0.15 — bid/ask gap that counts as "no market"
- `market_weight` 0.5 / `full_conf_games` 25 — how much the model is trusted, and how many rated games each side needs for full trust
- `kelly_fraction` 0.25, `max_units` 1.0 — stake sizing
- `staking` — master switch. False = compute, log and grade edges as paper, stake nothing.
- `max_disagreement` 0.15 — refuse to stake when the raw model differs from the market by
  more than this; a model that asserts a 15-point mispricing in a liquid market on a
  handful of rated games is uninformed, not right.
- `require_rating_edge` — never stake a side the raw Elo rates below its opponent.
- `min_price` 0.0 (off) — floor on the ask. Tested at 0.20–0.30 and it removed winners
  as well as losers, so price is only a proxy for the disagreement problem.
- per league `ticker_order` — `away_home` (US series: ticker ends with the home code),
  `home_away` (all soccer), `neutral`. ESPN's homeAway overrides it when available.
- per league `stake: false` — off for ATP/WTA until their ratings separate.
- `card.compact_leans` — collapse market-favourite picks to one line per league.
- `research:` block — `mode` (headlines / claude / off), per-run cap, headline days and count, and for claude mode the model, effort, searches, `near_edge`, `elo_per_point`, `demote_on_red_flag`
- per league: `k` (Elo K), `home_adv`, `neutral` (tennis), `min_games` (drops the ⚠️low-data tag), `espn: [sport, league]` + `injuries` / `injury_elo` / `weather`

## Checking how it's doing

- **Discord**: every picks card ends with the running EDGE record, units, ROI and average CLV.
- **On demand**: Actions → **Results Check** → *Run workflow* (`days` input, default 2). It grades
  every pick that has settled and posts a results card — ✅/❌ per pick with units and CLV — and
  **never logs new picks**, so it is safe to run at any hour. Locally: `python main.py --grade-only --days=3`.
  (A normal Daily Picks run also grades, but it logs that day's picks at whatever the prices are
  at that moment, so don't run it off-schedule just to check results.)
- **The files**: `data/v2/picks_log.csv` is every pick with its result, profit and CLV;
  `data/v2/stats.json` is the rolled-up record by tier, league, last 7 days and research bucket.

What to actually judge it on: units and ROI, not win rate — and early on, **CLV** (`clv` column,
`avg_clv` in stats). Positive average CLV means the market moved toward the bot after it bet,
which shows up long before W-L does. `brier_model` below `brier_market` means the model is
adding information the price didn't already have.

## Tracking an outside tipster

Paste someone's slate and EdgeBot logs it against the same Kalshi market it bets
itself, then grades it from the same settlement feed — so the comparison is
like-for-like rather than their record versus yours.

Actions → **Log Tipster Slate** → *Run workflow*: put their name in `name` (keep it
identical each time) and paste the post into `slate`. One pick per line, roughly
`A vs B - Pick ML`; headers, chatter and reactions are ignored. Tick `backfill`
for a past day — settled markets only report a closing price, so entry prices then
come from our own pick log instead. Locally:
`python tipsters.py --name fph0 --slate-file slate.txt [--backfill] [--date YYYY-MM-DD]`.

Each line is matched to a live Kalshi event by scoring **both** competitor names
against every event on the board, which tolerates surnames only ("Merida"),
abbreviations ("PSG", "NY City"), and typos ("Nashvile"). Bet types are recorded:
`ML` is scored, `ML+` scores the moneyline leg of a bigger ticket, and `SPREAD` /
`OTHER` are stored but never scored, so a handicap can't flatter the ROI.

`data/v2/tipsters_log.csv` is the dataset — per pick: the price and de-vigged
market probability at post time, what EdgeBot picked and its model probability,
whether they agreed, the result, CLV, and P/L on a flat $100. `stats.json` gains a
`tipsters` block per person: overall, **agrees_with_model**, and
**disagrees_with_model**.

Two columns do the real work when judging them:

- `expected_w` — wins the market price implied across those same picks. Beating it
  by a lot over many picks is edge; a hit rate on its own is not, since backing
  favorites wins often and still loses money.
- `price_src` — `live` is a real ask captured at post time and is the only fully
  trustworthy entry price. `eb_log` / `eb_log_derived` come from backfill and are
  approximations, so filter on this before training anything on the data.

The **disagrees_with_model** split is the one worth watching: when they take a side
our model rejects, are they right? That is where they'd be adding information the
model doesn't have.

## Setup / ops

1. Add the repo secret `DISCORD_WEBHOOK_URL`. Headlines research works with no extra setup;
   for `research.mode: claude` also add `ANTHROPIC_API_KEY` (cached per day, so the second run is free).
2. Actions → **Daily Picks** → **Run workflow**. The first run pulls Kalshi's full
   settled history for every enabled league, so allow a few minutes.
3. The workflow uses a concurrency group and `git pull --rebase -X theirs` before
   pushing, so two runs can't clobber each other's `data/` commit.

## Is it working?

**No — and staking is off because of it.** Over the first 306 graded picks:

| test | result |
|---|---|
| staked edges | **3-20**, −$1,238 flat, −53.8% ROI |
| the raw model's Brier vs the de-vigged Kalshi price | **0.058 worse** (95% CI +0.040 to +0.075) |
| picks where the model disagreed with the market most (raw − market ≥ 0.10) | **2-24** against 6.2 market-implied wins |
| paper leans | 186-90 against 187.8 market-implied — i.e. exactly the market |

There is no bucket — by league, price, confidence or disagreement — where the model
beat the market. The leans track the market because the blend *is* mostly the market:
with `model_w = (1 − market_weight) × conf` and a median `conf` around 0.2, the
"model's favourite" is the market's favourite about 84% of the time.

**Why the edges were all longshots.** `edge = (1 − market_weight) × conf × (raw −
market)`. Elo starts everyone at 1500 and tennis players had ~4-6 rated matches each,
so `raw` sits near 0.5 on every match while the market prices favourites up to 0.955.
The gap `raw − market` is therefore biggest exactly where the market is most
confident, and the positive-edge side is always the underdog: 21 of 23 staked edges
were priced under 40¢, and 20 of 27 were on the side the model's own Elo rated
*lower*. The filter wasn't finding value, it was converting "I have no information"
into "the longshot is underpriced".

### Turning staking back on

Set `staking: true` in config.yaml — but only once the paper record answers yes to
both: `brier_raw` below `brier_market` in `stats.json`, and the
`model_vs_market` block showing the model beating market-implied wins where it
actually disagrees, over 100+ graded picks it did not choose. The gates
(`max_disagreement`, `require_rating_edge`, per-league `stake`) stay on either way.

## Honest expectations

- Elo is built from Kalshi's settled history only, which reaches back weeks, not years.
  Leagues with few games per competitor (tennis: hundreds of players, ~5 matches each)
  cannot produce a meaningful rating and should not be staked.
- Win % means nothing. Units, ROI and — once there are enough snapshots — CLV are the
  truth, and `expected_w` (what the market priced those same picks at) is the yardstick.
- Kalshi lines are sharp. Sustained positive CLV and 3–5% ROI would be a very good result.
- ESPN and Open-Meteo are free public APIs; if one hiccups the pick still goes out
  without that note.

## Layout

Flat: `main.py` (orchestration + model), `kalshi.py` (market + results),
`espn.py` (injuries/venue), `weather.py`, `elo.py`, `edge.py` (de-vig, Kelly),
`state.py` (persistence + analytics), `research.py` (Claude web research),
`tipsters.py` (outside slates), `notify.py` (Discord, rate-limit aware),
`merge_state.py` (unions the append-only logs before each commit). Adding a factor is
one function plus one adjustment line in `model_game`.
