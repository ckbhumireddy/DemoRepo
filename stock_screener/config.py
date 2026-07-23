"""Configurable thresholds for the screener.

All tunables live here so the strategy can be adjusted without touching the
analysis code. Defaults aim at "high-quality large caps that recently sold off
on earnings" -- adjust to taste.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScreenConfig:
    # ---- Fundamental quality thresholds -------------------------------------
    min_market_cap: float = 2_000_000_000      # $2B: skip micro/small caps
    max_trailing_pe: float = 45.0              # avoid extreme valuations
    min_profit_margin: float = 0.05            # 5% net margin
    min_operating_margin: float = 0.08         # 8% operating margin
    min_return_on_equity: float = 0.12         # 12% ROE
    min_revenue_growth: float = 0.03           # 3% yoy revenue growth
    max_debt_to_equity: float = 2.0            # not over-levered
    min_current_ratio: float = 1.0             # can cover short-term liabilities
    require_positive_fcf: bool = True          # positive free cash flow

    # Minimum quality score (0-100) required to be considered.
    min_fundamental_score: float = 60.0

    # ---- Earnings-crash detection -------------------------------------------
    # How recently (calendar days) the earnings report must have happened.
    crash_lookback_days: int = 45
    # Trading days after earnings to look for the trough.
    post_earnings_window_days: int = 10
    # Minimum drop (as a positive fraction) to qualify. 0.08 = an 8% fall.
    min_crash_pct: float = 0.08
    # Require the stock to still be below its pre-earnings price (opportunity
    # hasn't fully closed). Set False to also surface names that have bounced.
    require_still_depressed: bool = True

    # ---- Ranking -------------------------------------------------------------
    # How much the composite score weights fundamental quality vs crash depth.
    quality_weight: float = 0.6
    crash_weight: float = 0.4
