"""The trader's own signal functions (pure).

Adapted from the trade-sheet analyzer so the trader's playbook can diverge
without coupling; only the data models are shared. Signals: post-earnings
reaction history, implied vs historical move, options liquidity, and trend
bias.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from earnings_analyzer.models import (
    ImpliedMove,
    LiquidityReport,
    OptionChain,
    OptionContract,
    PriceBar,
    QuarterResult,
    ReactionStats,
    TrendContext,
)

# An expiry landing more than this many days past the event includes so much
# non-event time value that the implied-move comparison stops being honest.
DILUTION_DAYS = 10


# --------------------------------------------------------------------------- #
# Reaction history
# --------------------------------------------------------------------------- #
def reactions_from_bars(
    bars: List[PriceBar], earnings_dates: List[dt.date]
) -> Dict[dt.date, float]:
    """Percent price move across each report: last close before it vs the
    first close after it. Dates the bars cannot bracket are omitted."""
    closes = sorted((b.day, b.close) for b in bars if b.close > 0)
    out: Dict[dt.date, float] = {}
    for ed in earnings_dates:
        before = [c for d, c in closes if d < ed]
        after = [c for d, c in closes if d > ed]
        if before and after and before[-1] > 0:
            out[ed] = (after[0] - before[-1]) / before[-1] * 100.0
    return out


def attach_reactions(
    quarters: List[QuarterResult], reactions: Dict[dt.date, float]
) -> List[QuarterResult]:
    """Fill in ``reaction_pct`` on quarters whose date has a computed move."""
    import dataclasses

    out = []
    for q in quarters:
        if q.reaction_pct is None and q.date in reactions:
            q = dataclasses.replace(q, reaction_pct=reactions[q.date])
        out.append(q)
    return out


def compute_reaction_stats(
    quarters: List[QuarterResult], min_samples: int = 4
) -> Optional[ReactionStats]:
    """Distribution of past post-earnings moves; ``None`` below min_samples."""
    moves = [q.reaction_pct for q in quarters if q.reaction_pct is not None]
    if len(moves) < min_samples:
        return None
    return ReactionStats(
        samples=len(moves),
        avg_abs_move_pct=sum(abs(m) for m in moves) / len(moves),
        up_rate=sum(1 for m in moves if m >= 0) / len(moves),
        best_pct=max(moves),
        worst_pct=min(moves),
    )


def _surprise(q: QuarterResult) -> Optional[float]:
    if q.surprise_pct is not None:
        return q.surprise_pct
    if q.eps_actual is not None and q.eps_estimate:
        return (q.eps_actual - q.eps_estimate) / abs(q.eps_estimate) * 100.0
    return None


def compute_streak(quarters: List[QuarterResult], max_quarters: int = 12) -> int:
    """Consecutive beat (+) or miss (-) streak, newest quarter first."""
    recent = sorted(quarters, key=lambda q: q.date, reverse=True)[:max_quarters]
    streak = 0
    for q in recent:
        s = _surprise(q)
        if s is None:
            continue
        if s >= 0:
            if streak < 0:
                break
            streak += 1
        else:
            if streak > 0:
                break
            streak -= 1
    return streak


# --------------------------------------------------------------------------- #
# Implied move
# --------------------------------------------------------------------------- #
def select_event_expiry(
    expiries: List[dt.date], event_date: dt.date
) -> Optional[dt.date]:
    """The nearest expiry on/after the event — where the event premium lives."""
    candidates = sorted(e for e in expiries if e >= event_date)
    return candidates[0] if candidates else None


def compute_implied_move(
    chain: OptionChain,
    spot: float,
    event_date: dt.date,
    hist_avg_abs_move: Optional[float],
    rich_threshold: float = 1.25,
    cheap_threshold: float = 0.80,
) -> Optional[ImpliedMove]:
    """ATM straddle debit / spot, compared against the historical move."""
    if spot <= 0:
        return None
    call, put = chain.atm_call(), chain.atm_put()
    if call is None or put is None:
        return None
    if call.mid is None or put.mid is None or call.mid <= 0 or put.mid <= 0:
        return None

    straddle = call.mid + put.mid
    implied_pct = straddle / spot * 100.0
    diluted = (chain.expiry - event_date).days > DILUTION_DAYS

    ratio = None
    verdict = "unknown"
    if hist_avg_abs_move and hist_avg_abs_move > 0 and not diluted:
        ratio = implied_pct / hist_avg_abs_move
        if ratio >= rich_threshold:
            verdict = "rich"
        elif ratio <= cheap_threshold:
            verdict = "cheap"
        else:
            verdict = "fair"

    return ImpliedMove(
        expiry=chain.expiry,
        straddle_debit=round(straddle, 2),
        implied_move_pct=round(implied_pct, 2),
        historical_avg_abs_move_pct=hist_avg_abs_move,
        ratio=round(ratio, 2) if ratio is not None else None,
        verdict=verdict,
        diluted=diluted,
    )


# --------------------------------------------------------------------------- #
# Liquidity
# --------------------------------------------------------------------------- #
def _avg(values: List[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def screen_liquidity(
    chain: OptionChain,
    min_open_interest: float = 200,
    min_option_volume: float = 50,
    max_spread_pct: float = 8.0,
) -> LiquidityReport:
    """Judge tradeability from the ATM call + put of the event-expiry chain.

    The spread is the only hard cost gate. Open interest and volume are
    *substitutes*, not independent gates: a momentum name that just gapped
    (like NBIS on its report day) always shows thin open interest at the new
    ATM strikes — open interest updates only overnight — while same-day
    volume is heavy. Either signal proves depth; only both thin fails.
    """
    atm: List[OptionContract] = [
        c for c in (chain.atm_call(), chain.atm_put()) if c is not None
    ]
    if not atm:
        return LiquidityReport(tradeable=False, reasons=["no ATM quotes"])

    oi = _avg([c.open_interest for c in atm])
    volume = _avg([c.volume for c in atm])
    spread = _avg([c.spread_pct for c in atm])

    reasons: List[str] = []
    oi_ok = oi is not None and oi >= min_open_interest
    volume_ok = volume is not None and volume >= min_option_volume
    if not oi_ok and not volume_ok:
        reasons.append(
            "thin depth: open interest "
            + (f"{oi:.0f}" if oi is not None else "n/a")
            + f" < {min_open_interest:.0f} and volume "
            + (f"{volume:.0f}" if volume is not None else "n/a")
            + f" < {min_option_volume:.0f}"
        )
    if spread is None or spread > max_spread_pct:
        reasons.append(
            f"spread {spread:.1f}% > {max_spread_pct:.0f}%"
            if spread is not None
            else "spread unavailable"
        )

    return LiquidityReport(
        atm_open_interest=oi,
        atm_volume=volume,
        atm_spread_pct=round(spread, 2) if spread is not None else None,
        tradeable=not reasons,
        reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# Trend
# --------------------------------------------------------------------------- #
def _ma(closes: List[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def compute_trend(bars: List[PriceBar], streak: int = 0) -> Optional[TrendContext]:
    """Moving averages, 52-week-range position, and a simple bias.

    Bias: bullish when price > MA50 > MA200 and the stock sits in the upper
    half of its 52-week range; bearish on the mirror image; else neutral.
    An earnings beat/miss streak of +/-3 or more breaks a neutral tie.
    """
    ordered = sorted(bars, key=lambda b: b.day)
    closes = [b.close for b in ordered if b.close > 0]
    if not closes:
        return None
    price = closes[-1]

    ma20 = _ma(closes, 20)
    ma50 = _ma(closes, 50)
    ma200 = _ma(closes, 200)

    year = closes[-252:]
    lo, hi = min(year), max(year)
    pct_range = (price - lo) / (hi - lo) if hi > lo else None

    bias = "neutral"
    if ma50 is not None and ma200 is not None and pct_range is not None:
        if price > ma50 > ma200 and pct_range >= 0.5:
            bias = "bullish"
        elif price < ma50 < ma200 and pct_range <= 0.5:
            bias = "bearish"
    if bias == "neutral":
        if streak >= 3:
            bias = "bullish"
        elif streak <= -3:
            bias = "bearish"

    return TrendContext(
        price=price,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        pct_of_52wk_range=pct_range,
        bias=bias,
    )
