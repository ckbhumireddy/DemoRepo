# S&P 500 Earnings Notifier

Emails you a daily digest of **S&P 500 companies reporting earnings within the
next week**. Each company stays in the digest every day until its earnings day
passes; a NEW badge marks its first appearance. Runs automatically every
weekday via GitHub Actions — no server to maintain.

## How it works

1. **Roster** — pulls the current S&P 500 constituents from Wikipedia at
   runtime (falls back to a bundled snapshot in `data/sp500_fallback.txt` if
   the fetch fails).
2. **Earnings dates** — looks up each ticker's next earnings date from Yahoo
   Finance (via `yfinance`).
3. **Selection** — keeps the companies whose earnings fall **within the next
   `LEAD_DAYS`** (default **7**) days (from `MIN_DAYS`, default today).
4. **Mark what is new** — remembers which `(ticker, date)` pairs were already
   emailed (small JSON state file) and gives first-time events a **NEW** badge.
   The company itself repeats in the digest daily until its earnings day.
5. **Notify** — when the window is not empty, renders a plain-text + HTML
   digest and emails it over SMTP (otherwise sends nothing).

```
earnings_notifier/
  config.py       env-driven configuration
  sp500.py        S&P 500 roster (Wikipedia + fallback)
  earnings.py     earnings model, yfinance provider, selection logic (pure)
  state.py        once-only "already notified" state (JSON)
  formatting.py   text/HTML email rendering (pure)
  notifier.py     SMTP delivery
  service.py      orchestration
  __main__.py     CLI
data/sp500_fallback.txt
.github/workflows/earnings-notifications.yml
tests/
```

## Quick start (local)

```bash
pip install -r requirements.txt

# Fully offline preview of the email layout (canned data, nothing sent):
python -m earnings_notifier --demo

# Real Yahoo Finance data, first 25 tickers, printed not sent:
python -m earnings_notifier --dry-run --limit 25

# Send for real (needs SMTP + recipients configured):
cp .env.example .env      # then edit
python -m earnings_notifier --env-file .env
```

### CLI options

| Flag | Meaning |
|------|---------|
| `--dry-run` | Render and print the digest; don't send email or write state |
| `--demo` | Use built-in sample data, fully offline (implies `--dry-run`) |
| `--lead-days N` | Look-ahead horizon in days (default 7) |
| `--min-days N` | Earliest days-out to include (default 0) |
| `--limit N` | Only look up the first N tickers (testing) |
| `--tickers A,B,C` | Use these tickers instead of the S&P 500 list |
| `--extra-tickers A,B` | Add these tickers on top of the S&P 500 (watchlist) |
| `--state-file PATH` | Where to store once-only state (default `state/notified.json`) |
| `--no-state` | Disable de-dup; report the whole window every run |
| `--send-empty` | Email even when nothing new is due |
| `--env-file PATH` | Load env vars from a file first |
| `-v` | Debug logging |

## Scheduled runs (GitHub Actions)

The workflow `.github/workflows/earnings-notifications.yml` runs weekdays at
**13:00 UTC** (~09:00 US Eastern, before the open) and can also be triggered
manually from the **Actions** tab (with an optional dry-run toggle).

Configure these in **Settings → Secrets and variables → Actions**:

**Secrets** (required):

| Secret | Example |
|--------|---------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASSWORD` | Gmail [App Password](https://myaccount.google.com/apppasswords) |
| `EMAIL_FROM` | `you@gmail.com` |
| `EMAIL_TO` | `you@gmail.com,teammate@example.com` |
| `SMTP_USE_TLS` | `true` (STARTTLS/587) |
| `SMTP_USE_SSL` | `false` (set `true` + port `465` for implicit TLS) |

**Variables** (optional): `LEAD_DAYS`, `MIN_DAYS`, `EXTRA_TICKERS`.

### Emailing each earnings only once

The job reports every earnings falling **within the next `LEAD_DAYS`** days.
Because an earnings date stays inside that window for several days, the service
keeps a small state file (`state/notified.json`) of `(ticker, date)` pairs it
has already emailed and skips them on later runs — so you get **one** heads-up
per earnings, the first day it enters the window. On days with nothing new, no
email is sent (set `SEND_EMPTY=true` to change that).

In GitHub Actions this state is persisted between runs via `actions/cache` (no
repo commits). Worst case, if the cache is ever evicted, you might get one
duplicate — harmless. Set `USE_STATE=false` to turn de-duplication off.

To track names **outside** the S&P 500 (e.g. `NBIS`), set the `EXTRA_TICKERS`
Actions variable to a comma-separated list — they're appended to the roster and
notified on the same one-week-ahead rule.

> **Gmail note:** use an App Password with 2-Step Verification enabled — your
> normal password will not work for SMTP. SendGrid, Mailgun, Amazon SES, etc.
> work the same way; just point `SMTP_HOST`/port/credentials at them.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The selection, formatting, config, and roster logic are covered by pure unit
tests that need no network. The yfinance and SMTP layers are thin wrappers
exercised via injected fakes.

## Notes & limitations

- Earnings dates from Yahoo Finance are often **estimates** until a company
  confirms; they can shift. The digest flags estimated vs. confirmed and
  reminds you to verify. If a date moves earlier after you were notified, the
  new date is a different `(ticker, date)` key and will trigger a fresh email.
- Looking up ~500 tickers takes a couple of minutes; the job allows 30.
- Not investment advice.

## Earnings Trade Sheet (analyzer)

A second service, `earnings_analyzer/`, runs right after the digest and emails
a separate **Earnings Trade Sheet**: a deep, rated analysis card for every
ticker in the window (fresh every run, no de-duplication).

Per ticker it computes:

- **Earnings history** (up to 12 quarters): beat rate, average surprise, streak
- **Reaction history**: average absolute post-earnings move, up rate, best/worst
- **Implied vs historical move**: ATM straddle at the event expiry vs the
  stock's realized earnings moves, with a rich / fair / cheap verdict
- **IV rank** (proxy: current ATM IV vs the trailing realized-vol range)
- **Options liquidity screen**: ATM open interest, volume, spread width --
  illiquid names get a NO TRADE badge and no suggestions
- **Trend context**: MA20/50/200, 52-week-range position, directional bias
- **Composite 0-100 rating** (A+..F) with a transparent component breakdown
- **Priced option strategies** keyed to the vol verdict and bias: iron condor,
  put/call credit spreads, debit spreads, long straddle, call calendar --
  each with real strikes, credit/debit, max profit/loss, and breakevens

Useful commands:

```
python -m earnings_analyzer --demo                    # offline preview, canned data
python -m earnings_analyzer --dry-run --tickers AAPL  # live-data preview
python -m earnings_analyzer                           # real email (reads out/window.json)
```

The notifier writes the window to `out/window.json` (env `WINDOW_FILE`); the
analyzer reads it, or falls back to `--tickers` / a full window recompute.
Tuning env vars: `HISTORY_QUARTERS`, `RICH_THRESHOLD`, `CHEAP_THRESHOLD`,
`MIN_OPEN_INTEREST`, `MIN_OPTION_VOLUME`, `MAX_SPREAD_PCT`,
`ANALYZER_MAX_WORKERS`, `ANALYZER_TICKER_LIMIT`, `ANALYZER_SEND_EMPTY`.

> Educational analysis only -- not investment advice. Quotes are Yahoo Finance
> mids and may be delayed; options involve substantial risk.

### Schwab real-time market data (optional)

With a Schwab brokerage + developer account, the analyzer can price chains
and history from the **Schwab Trader API in real time** (Yahoo stays as the
automatic fallback, and always supplies earnings history, which Schwab does
not offer):

1. Register an app at developer.schwab.com (callback `https://127.0.0.1`).
2. `gh secret set SCHWAB_APP_KEY` and `gh secret set SCHWAB_APP_SECRET`.
3. Weekly (Schwab refresh tokens hard-expire after 7 days):
   `python scripts/schwab_auth.py --set-secret` -- browser login, then the
   token lands in the `SCHWAB_TOKEN` secret automatically.

If the token lapses, runs keep working on Yahoo data -- quality degrades,
nothing breaks.

## Paper Trader (earnings_trader)

An independent paper-trading service. It finds candidates in the digest's
window file (tonight's after-market and tomorrow's pre-market reporters),
runs its own signal pipeline (implied vs historical move, liquidity, trend),
and trades one defined-risk option strategy per setup: $25,000 start, max
$5,000 risk per position. Emails: a daily plan + 30-day performance email
each morning, one email per trade opened, one per trade closed.

```powershell
python -m earnings_trader --phase morning --dry-run   # plan preview
python -m earnings_trader --phase entry --dry-run     # entry preview
```

### Paper trader triggers

GitHub's own cron starts runs 55-75 minutes late, so the workflow keeps its
crons only as a fallback. The primary trigger is an external scheduler that
calls the workflow_dispatch API at the exact minute. Setup (cron-job.org,
free tier):

1. Create a fine-grained GitHub token: github.com -> Settings -> Developer
   settings -> Fine-grained tokens. Repository access: only this repo.
   Permissions: **Actions: Read and write**. Copy the token.
2. At cron-job.org create two jobs, timezone **America/New_York** (this
   tracks DST automatically; the UTC crons in the workflow do not):
   - "trader-morning": weekdays 10:10, body `{"ref":"master","inputs":{"phase":"morning"}}`
   - "trader-entry":   weekdays 15:40, body `{"ref":"master","inputs":{"phase":"entry"}}`
3. Both jobs POST to
   `https://api.github.com/repos/ckbhumireddy/DemoRepo/actions/workflows/earnings-trader.yml/dispatches`
   with headers:
   - `Authorization: Bearer <token>`
   - `Accept: application/vnd.github+json`
   - `Content-Type: application/json`
4. Test from a shell (expects HTTP 204, then a run appears):
   `gh api repos/ckbhumireddy/DemoRepo/actions/workflows/earnings-trader.yml/dispatches -f ref=master -f "inputs[phase]=morning" -f "inputs[dry_run]=true"`

Double runs (external + cron fallback on the same day) are harmless:
position state dedupes opens and closes, and a ledger marker keeps the
daily plan email to one send per day.

## Portfolio Insights (portfolio_insights)

Watches your own portfolio (not held at Schwab — the holdings come from a
JSON document you maintain) and emails rule-based insights:

- **EOD email** (~16:35 ET): portfolio value, day and total P&L vs SPY, a
  positions table, biggest movers, upcoming earnings for held tickers, and
  watch items with soft suggestions (concentration, volatility spikes, big
  loss days, positions far under water, winner concentration creep).
- **Midday alert** (~12:30 ET): sent only when a position moves more than
  5% or +/-$5,000 on the day, or the portfolio more than 2% or +/-$15,000
  (percent OR dollars; thresholds configurable via `INSIGHTS_*` env vars
  or repository variables).

Market data comes from Schwab (quotes and history) with automatic Yahoo
fallback. Holdings never live in the repository.

### Updating the portfolio

1. Fidelity -> Accounts & Trade -> Positions -> download icon (CSV).
2. `python scripts/fidelity_to_portfolio.py Portfolio_Positions_<date>.csv`
   writes `portfolio.json` (gitignored) and lists any excluded option rows.
3. `gh secret set PORTFOLIO_JSON < portfolio.json` updates the CI secret.

Repeat after trades. Local previews:

```powershell
python -m portfolio_insights --mode eod --dry-run
python -m portfolio_insights --mode midday --dry-run
```

### Insights triggers

Same pattern as the paper trader: two more cron-job.org jobs (timezone
America/New_York, same PAT, same headers) POSTing to
`https://api.github.com/repos/ckbhumireddy/DemoRepo/actions/workflows/portfolio-insights.yml/dispatches`:

- "insights-midday": weekdays 12:30, body `{"ref":"master","inputs":{"mode":"midday"}}`
- "insights-eod":    weekdays 16:35, body `{"ref":"master","inputs":{"mode":"eod"}}`

The workflow's UTC crons are fallback only.

## IV Rank Scanner (iv_scanner)

Daily email at the market open: the top 50 S&P 500 names by IV rank
(current ~30-day ATM implied volatility vs the stock's own 1-year
realized-vol range — a proxy), with your portfolio holdings highlighted
and rule-based options-play ideas (covered calls on rich held names,
earnings premium candidates, premium-selling setups, cheap protection on
low-rank holdings). Quotes are from the prior close.

The full S&P 500 sweep needs Schwab market data (~9 minutes, rate-paced);
without it the scan degrades to portfolio + this week's reporters via
Yahoo. Trigger: add a cron-job.org job "ivscan" (weekdays 09:00,
America/New_York, same PAT/headers) POSTing
`{"ref":"master","inputs":{}}` to
`.../actions/workflows/iv-scanner.yml/dispatches`; the workflow's UTC
cron is fallback only.

```powershell
python -m iv_scanner --dry-run --universe portfolio   # fast local preview
```

## Market Insights — unusual volume (market_insights)

Daily email after the close: the S&P 500 names trading on genuinely unusual
volume, each read against its own short- and long-term trend. Volume alone
is noise, so every row carries an interpretation rather than just a
multiple — *Trend continuation*, *Distribution into strength*,
*Counter-trend rally*, *Downtrend acceleration*, *Breakout attempt*,
*Volume without direction*.

How "unusual" is measured:

- **RVOL** — the session's volume against the **median** of the last 20
  sessions. The median, not the mean, so one earnings-day spike inside the
  lookback cannot hide the next one.
- **Log z-score** — how far the day sits above the stock's *own* recent
  distribution. 2x is extraordinary for a mega-cap and routine for a small
  cap; the z-score is what makes those comparable.
- Liquidity floors ($5 price, $25M projected dollar volume) keep thin tape
  out of the email, where an 8x day on 40k shares would otherwise dominate
  the ranking.

Trend is scored on two horizons, each from three independent components so
one noisy input cannot flip a label: **short term** (5- and 21-session
returns, price vs the 20-day) and **long term** (6-month return, price vs
the 200-day, 50-day vs 200-day). The interesting names are usually the ones
where the horizons disagree — a heavy down day inside an intact uptrend
reads very differently from the same day in a downtrend.

The full S&P 500 sweep needs TradeStation market data and runs in two
stages: one `/quotes` call per 100 symbols screens the whole index (~5
requests), then only the hot names plus your holdings get a daily-history
request. Without TradeStation the scan degrades to your portfolio and
`EXTRA_TICKERS` watchlist via Yahoo.

Runs after the close by default, so the numbers are final. Dispatching it
mid-session works too: the partial day is projected from a typical
(U-shaped) intraday volume curve rather than a linear clock, and the email
says which basis it used.

```powershell
python -m market_insights --dry-run --universe portfolio   # fast local preview
python -m market_insights --dry-run --min-rvol 1.5 --top 40
```

### TradeStation market data (optional)

Register an app at [developer.tradestation.com](https://developer.tradestation.com)
with the `MarketData` and `offline_access` scopes, then run once:

```powershell
python scripts/tradestation_auth.py --set-secret
```

Unlike Schwab's 7-day refresh token, a TradeStation refresh token does not
expire — this is one-time setup, not a weekly chore. Set
`TRADESTATION_ENVIRONMENT=sim` to point at the simulation host.

## Martingale Paper Trader (martingale_trader)

A deliberate demonstration of why martingale position sizing fails,
played out on SPX with paper money. One long round per trading day,
close to close ($25,000 start):

- The stake is a notional exposure: **min(30% of balance × 3^step,
  $160,000)** — the champion of a 1990-2026 parameter search (worst
  CAGR across five start years, bust disqualifies, plus a 1.25x
  volatility stress gate). Percent sizing compounds with the account;
  the dollar cap bounds any single crash week.
- A losing day triples the stake; a winning day resets it to the 30%
  base; a flat day holds it. The notional may exceed the balance (paper
  leverage), because that is exactly the failure mode martingale hides.
  Set `MARTINGALE_BASE_PCT=0` for the original fixed-dollar ladder.
- The account **busts** when the balance reaches $0; a busted account
  never trades again.

One run per weekday at ~16:45 ET settles yesterday's round at the day's
close, opens the next one, and emails a daily report (result, balance,
ladder step, loss streaks, max drawdown, recent rounds). Closes come
from the **Schwab Trader API only** (`$SPX`) — no Yahoo fallback,
because this service writes permanent state from the prices it fetches:
a run without a live Schwab token fails and sends the failure alert
instead of trading on degraded data (keep the weekly
`scripts/schwab_auth.py --set-secret` refresh current). A duplicate run
or a market holiday is a no-op, so the external trigger and the cron
fallback can both fire. State (`state/martingale.json`) rides the
shared workflow cache.

Trigger: add a cron-job.org job "martingale" (weekdays 16:45,
America/New_York, same PAT/headers) POSTing `{"ref":"master","inputs":{}}`
to `.../actions/workflows/martingale-trader.yml/dispatches`; the
workflow's UTC cron is fallback only.

Sizing comes from a named preset in
`martingale_trader/champions.json` (variable
`MARTINGALE_CHAMPION`, default **tripledip**; `classic` is the
original fixed-dollar demo ladder; `tripledip-harvest` is a
research-only income variant — withdraw $25k at $75k — and
`doubledip-harvest` its risk-reduced sibling (40% base, x2 ladder:
same $350k banked, max drawdown 22.5% vs 36.3%), and
`compound-harvest` the high-cash point of the frontier ($480k banked
in 2 harvests at 37.9% drawdown, 2x leverage clamp); the live trader
refuses all three because it has no withdrawal support). Individual overrides:
`MARTINGALE_START_BALANCE`, `MARTINGALE_BASE_PCT`,
`MARTINGALE_BASE_NOTIONAL`, `MARTINGALE_FACTOR`,
`MARTINGALE_MAX_NOTIONAL`.

```powershell
python -m martingale_trader --dry-run   # preview, nothing traded or sent
```

> Schwab posts the official daily candle hours after the close, so
> the trader also reads the day's 30-minute candles and synthesizes
> the close once the session is complete (a mid-session run never
> sees a partial day — it falls back to the last posted daily bar).
> Educational only; martingale does not create an edge, it only
> reshapes when the losses arrive.

## Iron-Condor Martingale (condor_trader)

Martingale sizing applied to defined-risk option structures: one short
**SPXW weekly iron condor** at a time (modeled on a reference trade:
$5-point wings, short strikes ~1.25% out-of-the-money each side,
nearest expiry 5-12 days out), held to cash settlement at the expiry
close. The ladder sizes the CONTRACT COUNT from a
quantity table (default **1, 2, 7, 20, 51** — a levelQuantityMap
recovery sequence; set `CONDOR_QTY_LADDER` to a JSON list, comma list,
or level map), a losing week moves to the next rung, a winning week
resets to the first. Risk per position is capped at `CONDOR_MAX_RISK`
(default $25k) AND the balance — so unlike the naked-index martingale,
the account cannot bust; it can only shrink until it is too small to
trade.

One run per weekday (~16:50 ET, same cron-job.org + fallback-cron
pattern; POST to `.../workflows/condor-trader.yml/dispatches`): settle
any expired condor at that day's close, advance the ladder, open the
next setup from the live Schwab chain, and email what happened. Chains
and closes are Schwab-only. State: `state/condor.json`. Tuning:
`CONDOR_START_BALANCE`, `CONDOR_OTM_PCT`, `CONDOR_WING`,
`CONDOR_MIN_DTE`/`MAX_DTE`, `CONDOR_MIN_CREDIT`,
`CONDOR_QTY_LADDER`, `CONDOR_MAX_RISK`.

```powershell
python -m condor_trader --dry-run   # preview, nothing traded or sent
```

> A condor loses several times its credit, so a doubling ladder needs
> multiple wins to recover one loss — the demonstration here is how
> fast defined-risk sizing hits its cap. Educational only.

## Ladder Research (martingale_research)

The offline autoresearch harness that produced the **tripledip**
champion. It replays ladder configs continuously over SPX closes since
1990 (Yahoo, cached to `state/spx_closes.csv`) and scores each config
by its **worst CAGR across five start years** (1990/2000/2007/2014/2020)
— any bust disqualifies. Every run appends to
`state/research_results.tsv`.

```powershell
# score a named champion, with the 1.25x volatility stress gate
python -m martingale_research --champion tripledip --stress

# run a grid experiment
python -m martingale_research --tag exp1 --grid '[{"base": 0.3, "pct": true, "factor": 3.0}]'

# promote the best stress-surviving config into champions.json
python -m martingale_research --tag exp1 --grid '[...]' --promote mychampion
```

`--promote` writes the winner into `martingale_trader/champions.json`
in the live trader's schema — commit the file and set
`MARTINGALE_CHAMPION=<name>` to deploy it. Configs using
research-only knobs (`enter_after`, `max_lev`, non-full `reset`) are
refused, because the live trader cannot execute them. Promotion
requires surviving the stress gate; raw-history winners that die under
1.25x moves are not deployable artifacts.
