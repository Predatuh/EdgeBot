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

## Ratings (`backfill_elo.py`)

Elo was trained only on Kalshi's settled results — weeks of history, and no scores, so
margin of victory was off. College football sat at a **median of two rated games per
team**: every team near 1500, and every "edge" an artefact of that.

`backfill_elo.py` rebuilds a league from ESPN's historical scores instead. For college
football that is 2,887 games over three seasons, with margins:

| | before | after |
|---|---|---|
| rated games | 329 | 2,887 |
| rating spread (sd) | 16.7 | 135.7 |
| rating range | 74 | 771 |
| games per team (median) | 2 | 8 |

```
python backfill_elo.py --league ncaaf --seasons 3 --dry-run   # report, write nothing
python backfill_elo.py --league ncaaf --seasons 3
```

Ratings record `_espn_through`; the Kalshi ingest skips anything on or before it, so no
game is taught twice. The same history merges into `hist_*.json`, which is what form and
H2H read. Re-run it when Kalshi starts listing teams it has not seen before — a team
absent from the Kalshi vocabulary keeps its ESPN name and will not join up later.

**Names are the joint.** The table is keyed by Kalshi's `Ohio St.` while ESPN says
`Ohio State Buckeyes`. `match_score` ranks candidates by where the match starts, how many
characters match, and the parenthetical; ties are then retried against names already
claimed. That is what separates Miami (FL) from Miami (OH), Duquesne from Duke,
Jacksonville St. from Jackson St., and Colorado from Buffalo.

**A rating is not a verdict on schedule strength.** North Dakota St. came out of the
first backfill at 1888 — above Texas and Alabama — on 39 wins of which two were against
anyone rated 1600+. Elo assumes everyone eventually plays everyone; FCS and FBS barely
meet, so the divisions float apart and MOV widens the gap. The backfill reports these
rather than shrinking them, because a rating built on a weak schedule is unknowable
rather than wrong, and a silent correction would be a guess dressed up as a fix.

## Honest expectations

- Better ratings are necessary, not sufficient. The calibration study said the model
  loses to the market; the backfill removes the most obvious reason why, but whether it
  now beats the price is an empirical question. Keep `staking: false` until
  `model_vs_market` in `stats.json` says otherwise.
- Leagues with few games per competitor (tennis: hundreds of players, ~5 matches each)
  cannot produce a meaningful rating and should not be staked.
- Win % means nothing. Units, ROI and — once there are enough snapshots — CLV are the
  truth, and `expected_w` (what the market priced those same picks at) is the yardstick.
- Kalshi lines are sharp. Sustained positive CLV and 3–5% ROI would be a very good result.
- ESPN and Open-Meteo are free public APIs; if one hiccups the pick still goes out
  without that note.

## Signals beyond the price (`flow.py`, `epa.py`)

Added after 611 graded picks showed the model losing to the market. **Neither
gates a pick.** Both are recorded on every pick and bucketed in `stats.json`
(`by_flow`, `by_epa`) so the graded record decides whether they earn a say.

### `flow.py` — what the market did before we got here

- **Price history.** Kalshi's candlesticks, which the backtest already proved
  out, give a bid/ask back to when the market listed. Where a side opened vs
  where it is now is the exchange's line movement, and movement is the one thing
  in betting markets that reliably carries information.
- **The tape.** `/markets/trades` gives every print with its size and which side
  crossed the spread. Count the prints and you have "tickets"; sum the contracts
  and you have "money". 80% of prints on one side with 41% of the contracts is
  the same disagreement a sportsbook split is pointing at. Taker side only — a
  resting order that gets hit is not making the argument.

- **The book.** `/markets/{ticker}/orderbook` gives the resting size at every
  price on both sides - what is still waiting, as opposed to what already
  happened. Size near the touch is kept separate from size parked ten cents away.

```
python flow.py --check KXNCAAFGAME --n 2     # read it live off the busiest markets
```

A real reading:

```
LSU sat at 58c from 60c over 95h
tape 76% of 500 trades / 84% of 33790 contracts, biggest 4730
book 60% of the size near the touch
```

`toward` does not mean *good*: it means the market already agreed and the price
is worse than it was. `away` means early, or wrong. Which one is what the record
is for.

### `epa.py` — a rating that doesn't come from who won

Elo only knows results, so a good team losing close games and a bad team winning
them look identical until the results diverge. EPA per play separates them now.

| League | Source | Key needed |
|---|---|---|
| NFL | nflverse play-by-play (nflfastR's own `epa`) | none |
| NCAAF | collegefootballdata.com PPA | free `CFBD_API_KEY` secret |

```
python epa.py --league nfl,ncaaf      # writes data/v2/epa_<league>.json
python test_signals.py                # 50 offline checks on both modules
```

Three things that matter more than the source:

- **It is a second opinion, not a nudge.** It blends into the model's own
  probability at `epa_weight` (0.35 for both football codes). Added to Elo as an
  adjustment instead — which is how I wrote it first — it made a good team a
  717-Elo favourite, because Elo and EPA know mostly the same things and stacking
  them counts the same evidence twice.
- **Last season is carried behind this one.** A 17-play week-one sample said
  Jacksonville had the best offence in football. The prior fades on its own as
  real plays accumulate (NFL) or on the calendar through week 8 (college).
- **Shrunk and capped.** Season-to-date efficiency overstates how different teams
  are; it is regressed toward the mean and capped at three touchdowns.

Garbage time — plays outside 10–90% win probability — is excluded. Without a CFBD
key the college half is skipped loudly rather than guessed at.

**Kalshi also lists `KXNFLSPREAD`, `KXNFLTOTAL`, `KXNCAAFTEAMTOTAL`** and quarter
and half markets for both codes. We only trade the winner market, which is a
bigger gap than either signal above.

## Parlays (`parlay.py` + `webapp/`)

A separate tool. It does not use Elo, the edge model or staking — it builds combos out
of Kalshi's own prices, because the record says the market is the better estimate and a
parlay is a market product, not a model product.

One fetch pulls every league; **scopes** are one-tap filters over that board — Football
(NFL + NCAAF + CFL), NFL alone, College alone, Soccer, Tennis, Baseball, Cricket, or
Everything. Switching between them on the phone is instant and needs no connection.

**Draws.** Soccer and Test cricket price three outcomes, and a draw settles a win
contract at zero. A two-way de-vig would report P(win | no draw) — 55¢ for a leg really
worth 40¢. The draw stays in the denominator, and any leg that can be drawn is badged.

**The one fact it is built around:** Kalshi prices a combo as the product of its legs,
so a fairly priced parlay wins `1 / payout` of the time. A 3-leg ticket paying 11× and a
39-leg ticket paying 11× are the same 9% bet — the long one just crosses 39 spreads to
get there. Leg count is not the risk; the payout is. **You pick the payout; the builder
returns the highest win probability that still pays it.**

**There is no price floor, and that is the whole correction.** The first version targeted
a win probability behind a hard floor on each leg, which was backwards. Kalshi's spread is
about 1¢ whatever the contract costs, so a cent on a 97¢ leg is 1% of its value and a cent
on a 50¢ leg is 2% — but reaching a payout out of 97¢ favourites takes far more of them,
and you cross a spread every time. Measured on a real board:

| to win | chalk only (≥88¢) | any price allowed |
|---|---|---|
| 5× | 27 legs, 15.4%, **−21.5% EV** | 8 legs, 15.7%, **−0.0% EV** |
| 10× | *unreachable* | 9 legs, 8.9% |
| 250× | *unreachable* | 15 legs, 0.2% |

Same win chance, same payout, a third of the legs. Above 5× the chalk route does not
exist at all — which is why a "Lottery" rung used to hand back 1.3×. So a 55¢ game earns
its place whenever it buys payout more cheaply than another favourite would.

What protects you is the book, not the price: `is_liquid()` requires a spread of 3¢ or
tighter on a market that is actually trading. And `discount()` docks legs under 90¢ by
how much that price band has historically underperformed, so a coin flip has to be
genuinely cheap to get picked — `trust_cheap=True` turns that off.

```
python parlay.py                                   # print the football ladder
python parlay.py --scope nfl                       # NFL only
python parlay.py --scope all                       # every sport Kalshi lists
python parlay.py --html board.html                 # the phone app, one self-contained file
python parlay.py --json data/v2/parlay.json --discord
python parlay.py --offline --html board.html       # no network; page falls back to an example board
python parlay.py --from-json data/v2/parlay.json --html board.html   # re-render a saved board
python test_parlay.py                              # 90+ offline checks on the math
```

`--scope` only chooses which ladder gets printed and posted to Discord; the page always
carries every scope.

`.github/workflows/parlay.yml` runs it daily, posts the card to Discord, and writes
`docs/index.html`, which GitHub Pages serves at **https://predatuh.github.io/EdgeBot/**
(Settings → Pages → Deploy from branch → `main` → `/docs`). It installs to a phone home
screen from there; the manifest and icons ship with it.

### Paper parlays (`paper.py` + `.github/workflows/paper.yml`)

A ledger both sides bet into. The bot buys one ticket per rung every day at the
prices on the book at that moment; yours go in beside them, so the comparison is
like for like. Nothing is staked and nothing is real - it is practice with an
honest scoreboard.

```
python paper.py --grade --mark --place --scope football --stake 10
python paper.py --add "TICKER-A,TICKER-B" --owner you --label "Saturday"
python paper.py --cash-out b-20260914-0243-swing     # sell at the last mark
python test_paper.py                                 # 47 offline checks on the money
```

The arithmetic, which is the whole thing:

- A ticket **costs** the product of its legs' asks - that is where the payout
  comes from. It is **worth** the product of their bids, which is what selling it
  would fetch. So a ticket is under water by the spread the moment it is bought.
  That is the honest picture, not a bug.
- **Grading reads the market, never a model.** One losing leg ends the ticket
  immediately; a voided leg drops out and shrinks the payout the way a book drops
  it. An event where nobody won is void, not a clean sweep of losses.
- Every payout is worked out from the **entry price**. A later quote moves the
  mark; it can never move what a settled ticket paid.

**How the bot learns from it.** `paper.py` measures, per price band, how often
legs bought at that price actually won, and writes the drag - hit rate over what
was paid - to `data/v2/paper/calibration.json`. Once a band has 60 settled legs,
that measured number replaces the hand-set one in `parlay.py`'s `discount()`, so
which legs the builder thinks are worth buying is steered by results instead of
by a guess. It is capped at 1.0 deliberately: a band that measured *better* than
its price is a small sample telling you that you beat the market, and building on
that belief is how a paper record turns into a real loss. The effective table is
published in the board so the phone builds the same ticket the bot would.

**Why pricing only happens in the workflow.** Kalshi refuses any request carrying
an `Origin` header. Measured from a runner, one variable at a time:

| request | result |
|---|---|
| bot UA, no Origin | 200 |
| bot UA, with Origin | **403** |
| browser UA, no Origin | 200 |
| browser UA, with Origin | **403** |
| `OPTIONS` preflight | **403** |

That is a refusal, not a missing CORS header, so no page in any browser can quote
a price however it asks. The app therefore keeps your tickets on the phone at the
last published board price, re-prices them whenever a new board lands, and grades
them against `data/v2/settled.json`. **Send to the bot** copies the tickers; paste
them into the Paper Parlays workflow and they get priced at the real book and
counted towards what the bot learns.

### On Android, as a real app

**https://predatuh.github.io/EdgeBot/get.html**

Open that on the phone and tap Download. It is served straight off the Pages site,
one hop from the host the app already loads from - the release download
(`/releases/latest/download/gridiron-ticket.apk`) works too, but it redirects
twice onto a signed URL on another host, and an app's built-in browser often
cannot finish that. If a tap does nothing, you are in one of those browsers:
long-press and choose Open in Chrome. `android/` is a WebView around the same page, built
and signed by `.github/workflows/android.yml` and attached to the `app` release. Updates
install over the top, and the board keeps updating itself without a reinstall — the APK
only needs rebuilding when the app itself changes. `android/README.md` covers the signing
key and why the page is served from `https://appassets.androidplatform.net` rather than
`file://`.

The page carries a board baked in, so it renders instantly and works with no signal, then
asks `raw.githubusercontent.com` whether a newer one exists and adopts it if so. That is
what makes a single saved copy keep working: it is only a snapshot when it has to be. A
failed fetch is silent and harmless — the sandbox an Artifact runs in blocks it, and a
phone with no signal has nothing to ask — and a board that comes back empty or malformed
is rejected rather than allowed to replace a good one. The ↻ button forces a check; the
header says how old the board actually is.

Rungs are payouts: Banker 1.15× → Solid 2× → Swing 8× → Longshot 40× → Lottery 250×.
Win probability is about `1/payout` however a ticket is built, so that ladder runs from
~87% to ~0.4%. A board that cannot reach a rung says so and offers the payout it *can*
reach, rather than relabelling a small ticket.

Research runs on `candidates()` — the legs the builder actually wants across every scope
and rung — not on the highest prices. Ticket legs are researched first, then the rest of
the pool up to `research.max_per_run`. A second pass after flags land covers the
replacement that actually sits on the slip. Findings are attached to the leg and shown
in the app; only an injury we can pin on that team filters anything out.

Weather is the hourly forecast at kickoff, not "current" at run time. College injuries
come from CFBD (`CFBD_API_KEY`); ESPN has no injuries block for NCAAF. MLB legs carry
the probable starters. NFL Elo folds nickname duplicates (`GB Packers` → `Green Bay`)
so an ESPN backfill can actually separate the league.

Each leg and the whole slip link out to Kalshi. Pinning a pick adds it to this ticket
without restuffing the rung. Turn on "Ping me when the board lands" for a notice when
the morning snapshot is new (Discord still posts either way).

Leagues come from `config.yaml`, plus `parlay.FOOTBALL` for anything config lacks (CFL).
`enabled: false` and `stake: false` are about what the bot *rates*, and there is no rating
in a parlay, so those leagues are still offered here.

## Layout

Flat: `main.py` (orchestration + model), `kalshi.py` (market + results),
`espn.py` (injuries/venue), `weather.py`, `elo.py`, `edge.py` (de-vig, Kelly),
`state.py` (persistence + analytics), `research.py` (Claude web research),
`backfill_elo.py` (rebuild ratings from ESPN scores),
`tipsters.py` (outside slates), `notify.py` (Discord, rate-limit aware),
`merge_state.py` (unions the append-only logs before each commit),
`parlay.py` + `webapp/parlay.html` (the football parlay builder and its phone app),
`names.py` (canonical NFL/soccer keys), `cfbd.py` (college injuries).
Adding a factor is
one function plus one adjustment line in `model_game`.
