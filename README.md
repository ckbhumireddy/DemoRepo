# Earnings-Crash Quality Screener

Find **fundamentally strong companies that recently sold off on an earnings
report** — potential mean-reversion candidates — and get **educational options
strategy ideas** for expressing a recovery thesis.

> ⚠️ **Not financial advice.** This is a research and education tool. It does
> **not** place trades and makes no guarantees. Options carry a substantial risk
> of loss and can expire worthless. Do your own research and consider a licensed
> advisor before trading.

---

## The idea

The strategy targets a specific, well-known setup:

1. **Good foundation** — the company is genuinely high quality: profitable,
   growing, cash-generative, not over-levered, reasonably valued.
2. **Crashed on earnings** — the stock dropped sharply around its most recent
   earnings report (a knee-jerk reaction, guidance scare, or one-off miss),
   and hasn't fully recovered yet.
3. **Options to trade the rebound** — because post-earnings implied volatility
   is usually elevated then collapses ("IV crush"), the tool leans toward
   **defined-risk, premium-selling** strategies alongside longer-dated bullish
   plays.

Each stock gets a **quality score (0–100)**, a measured **crash depth**, and a
blended **composite score** used for ranking.

## Install

```bash
git clone <this-repo>
cd DemoRepo
python -m pip install -r requirements.txt   # installs yfinance + pytest
```

Python 3.9+ is required. Live scanning uses free
[Yahoo Finance](https://pypi.org/project/yfinance/) data (no API key). The
offline `demo` command and the whole test suite run **without** any network or
third-party data.

## Usage

Try it instantly with built-in synthetic data — no network needed:

```bash
python -m stock_screener.cli demo --detail
```

Scan the default large-cap watchlist with live data:

```bash
python -m stock_screener.cli screen
```

Scan specific tickers with full detail:

```bash
python -m stock_screener.cli screen --tickers AAPL NKE PYPL LULU --detail
```

Loosen the filters and emit JSON (for piping into other tools):

```bash
python -m stock_screener.cli screen --min-crash 0.05 --min-score 50 --json
```

Scan a custom list from a file (one ticker per line or comma-separated,
`#` comments allowed):

```bash
python -m stock_screener.cli screen --tickers-file my_watchlist.txt
```

### Useful flags

| Flag | Meaning |
|------|---------|
| `--tickers T1 T2 …` | Explicit tickers to scan |
| `--tickers-file PATH` | Read tickers from a file |
| `--min-crash 0.08` | Minimum earnings drop to qualify (0.08 = 8%) |
| `--min-score 60` | Minimum fundamental quality score (0–100) |
| `--lookback 45` | Only count earnings within this many days |
| `--include-recovered` | Also show names that already bounced back |
| `--detail` | Full per-candidate breakdown + options ideas |
| `--json` | Machine-readable JSON output |
| `--limit N` | Cap the number of results |

## How it works

```
stock_screener/
├── config.py              # all tunable thresholds (ScreenConfig)
├── data/
│   ├── models.py          # dataclasses (Fundamentals, PriceBar, Candidate, …)
│   ├── provider.py        # MarketDataProvider interface + InMemoryProvider
│   └── yahoo.py           # YahooProvider (yfinance, imported lazily)
├── analysis/
│   ├── fundamentals.py    # weighted quality scoring rubric
│   ├── earnings.py        # post-earnings crash detection
│   ├── options.py         # educational options-strategy suggestions
│   └── screener.py        # ties it together, ranks candidates
├── universe.py            # default ticker watchlist
├── report.py              # table / detail / JSON rendering
├── demo_data.py           # offline synthetic scenarios
└── cli.py                 # command-line interface
```

Data access sits behind a `MarketDataProvider` interface, so the analysis and
CLI never import `yfinance` directly. That keeps everything testable offline and
makes it straightforward to add a paid data provider (Polygon, Finnhub, …) later
without touching the rest of the code.

### Quality scoring

`analysis/fundamentals.py` runs a weighted, criterion-based rubric across
profitability (margins, ROE), growth, cash flow, balance-sheet health, and
valuation. Metrics missing from the data feed are **excluded** from the score
rather than penalized, and a `data_coverage` figure tells you how complete the
picture was.

### Crash detection

`analysis/earnings.py` looks at price history around the last earnings date and
measures the immediate reaction, the worst point (trough) in a short window
after, and where the stock sits now versus before the report. A qualifying
crash exceeds `min_crash_pct`, happened within `crash_lookback_days`, and (by
default) is still below its pre-earnings price.

## Running the tests

```bash
python -m pytest
```

The suite (16 tests) covers fundamentals scoring, crash detection, options
suggestions, and the end-to-end screener — all offline.

## Roadmap ideas

- Real options-chain pricing & implied-volatility rank (needs a data source
  with an options feed)
- Paper-trading integration (e.g. Alpaca) to track hypothetical P&L
- A web dashboard (Streamlit or FastAPI)
- Backtesting the "quality + earnings crash" edge over history
- Sentiment / guidance parsing to distinguish one-off misses from real breaks

## Disclaimer

This software is provided for educational and informational purposes only, with
no warranty of any kind. It is not investment advice and not a recommendation to
buy or sell any security or options contract. Trading options can result in the
loss of your entire investment. You are solely responsible for your own trading
decisions.
