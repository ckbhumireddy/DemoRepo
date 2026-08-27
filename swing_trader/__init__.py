"""Swing-trading service: pluggable strategies gated by backtests.

The rule this package enforces: **no strategy trades on live data until a
backtest has passed**. Each strategy is a pure function from price history
to signals; the same function drives both the backtest replay and the live
scan, so what was tested is exactly what runs. Backtest results are recorded
in a registry, and the daily scan refuses to email signals from any strategy
whose recorded backtest has not cleared the approval thresholds.

Strategy 1 — "distressed-sr": large-cap names in a deep drawdown (the S&P
500 stands in for "high market cap"; a 30%+ fall from the 52-week high for
"distressed"), traded between their own local support and resistance. The
levels come from swing pivots clustered into zones; entries trigger near a
tested support with a resistance target far enough away to pay for the risk.

Shares plumbing with the sibling packages (TradeStation session + quotes,
PriceBar model, S&P 500 roster, rate gate, email sender).
"""
