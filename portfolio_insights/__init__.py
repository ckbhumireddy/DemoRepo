"""Portfolio insights service.

Pulls the user's live positions from the Schwab Trader API (holdings never
live in the repo), joins them with market quotes, and emails rule-based
insights: an end-of-day summary with watch items and soft suggestions, and
a midday alert only when a position or the portfolio crosses a movement
threshold. Shares only plumbing with the sibling packages (Schwab session,
market-data provider, option/price models, email sender).
"""
