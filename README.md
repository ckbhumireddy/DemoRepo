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
