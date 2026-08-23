"""Market insights from the TradeStation API: unusual volume in trend context.

Each run sweeps a universe (S&P 500 + portfolio + watchlist), finds the names
trading on genuinely unusual volume, and reads that volume against the
stock's own short-term (days-to-weeks) and long-term (months) trend. Volume
alone is noise; volume plus direction is a signal, so the email leads with
the interpretation — accumulation, distribution, breakout, capitulation —
rather than a bare list of movers.

Shares plumbing with the sibling packages (PriceBar model, S&P 500 roster,
portfolio loader, rate gate, email sender); the TradeStation session and
payload parsers are the only genuinely new infrastructure.
"""
