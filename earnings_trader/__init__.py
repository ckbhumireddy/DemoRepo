"""Independent earnings paper-trading service.

Finds candidate tickers from the daily digest's window file, analyzes them
with its own signal pipeline, and paper-trades defined-risk option
strategies around the earnings report: entries near the market close before
the report, exits on the first run after it. Sends its own emails: a daily
plan + performance email, one email per trade opened, one per trade closed.

Shares only plumbing with the sibling packages (market-data provider,
option data models, email sender); every trade decision lives here.
"""
