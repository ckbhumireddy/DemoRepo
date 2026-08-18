"""Daily IV-rank scanner.

Scans the S&P 500 plus the user's portfolio tickers at the market open,
ranks every name by IV rank (current ~30-day ATM implied volatility vs the
stock's own trailing 1-year realized-volatility range — a proxy, since
free feeds expose no implied-vol history), and emails the top names with
portfolio holdings highlighted and rule-based options-play ideas.

Shares only plumbing with the sibling packages (Schwab session + payload
parsers, IV-rank math, S&P 500 roster, portfolio loader, email sender).
"""
