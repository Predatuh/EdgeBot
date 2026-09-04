# 🤖 EdgeBot — Multi-Sport Value Picker

Runs **entirely in the cloud on GitHub Actions** (free). No laptop needed —
set it up and manage it 100% from your phone. Twice a day it:

1. Pulls yesterday's finals → **auto-grades its own picks** and updates Elo ratings
2. Pulls today's board for **NFL, College Football, MLB, Tennis (ATP/WTA),
   Soccer (EPL / La Liga / Ligue 1), Cricket (beta)**
3. Layers in **injuries (players OUT), venue, weather (temp/wind/precip),
   home advantage, margin-of-victory-weighted form**
4. Compares its win probability to the **market odds (de-vigged)** and posts
   **only the picks with a real edge** to your Discord — everything else is a PASS
5. Keeps a **permanent P/L log** (`data/picks_log.csv`) with units won and ROI,
   so the record is provable, not vibes

## 📱 Phone-only setup (10 minutes)

1. **Discord webhook**: In your Discord server → Settings → Integrations →
   Webhooks → New Webhook → pick a channel → **Copy Webhook URL**.
2. **GitHub repo**: On github.com (mobile browser), create a new repo (private
   is fine). Tap **Add file → Upload files** and upload everything in this
   folder (keep the folder structure — easiest is uploading the zip contents
   folder by folder; the `.github/workflows/` path must be exact).
3. **Add the secret**: Repo → Settings → Secrets and variables → Actions →
   **New repository secret** → Name: `DISCORD_WEBHOOK_URL`, Value: your webhook URL.
4. **Enable Actions**: Repo → Actions tab → enable workflows →
   open **Daily Picks** → **Run workflow** to test it right now.
5. Done. It runs itself at 9 AM and 5 PM ET every day. Change times in
   `.github/workflows/daily-picks.yml` (cron is in UTC).

## ⚙️ Tuning (edit `config.yaml` from your phone)

- `edge_threshold: 0.04` — raise to 0.06 for fewer, stronger picks
- Turn leagues on/off with `enabled: true/false`
- `show_passes` — post the games it's skipping (recommended: accountability)

## ⚠️ Honest expectations

- **The first 2–4 weeks are calibration.** Elo starts from scratch, so the bot
  intentionally PASSES (`cold start`) until each league has enough rated games.
  Don't bet the early output — let the log prove itself first.
- Win % means nothing; **units and ROI in `data/picks_log.csv` are the truth.**
- Books/exchanges set sharp lines. A sustained 3–5% ROI is an excellent result;
  anyone promising 80% winners is selling favorites, not edge.
- Data comes from free public APIs (ESPN + Open-Meteo). If ESPN changes a
  format, a league may error for a day — the bot skips it and carries on.

## 🧱 Extend it

Each factor is a module: `espn.py` (scores/odds/injuries), `weather.py`,
`elo.py`, `edge.py`. Adding a data source = one function + one adjustment line
in `bot/main.py`. Ideas next: starting pitchers (MLB), QB status (NFL),
surface-specific tennis Elo, closing-line-value tracking.
