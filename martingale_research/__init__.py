"""Offline backtest harness for martingale-style ladders on SPX.

Autoresearch-style loop (github.com/karpathy/autoresearch): a fixed
evaluation harness scores ladder configurations; the researcher edits
the grid between runs and every run appends to a results log. This is
a research tool — it reads Yahoo daily closes and never touches the
live trader's state or Schwab quota.
"""
