"""Turning volume and trend into a readable signal (pure functions).

A list of stocks trading 4x their usual volume is not an insight — the same
4x means opposite things depending on which way the stock is moving and what
trend it is moving against. This module does that reading: it combines the
direction of the volume day with both trend horizons and names the pattern.

The classifications are descriptive, not predictive. "Distribution into
strength" says heavy selling is hitting a stock still in an uptrend; it does
not say what happens next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .trend import DOWN, UP, TrendView
from .volume import VolumeStats

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"

# A day's move below this is not enough to call the volume directional.
DIRECTIONAL_MOVE = 0.02

# RVOL tiers used for the headline wording.
EXTREME_RVOL = 5.0
HEAVY_RVOL = 3.0

# Within this much of a 52-week extreme, the level itself is worth saying.
RANGE_EDGE = 0.05


@dataclass
class Insight:
    """The named pattern plus one line of why."""

    label: str
    note: str
    direction: str = NEUTRAL


def intensity(rvol: float) -> str:
    if rvol >= EXTREME_RVOL:
        return "extreme"
    if rvol >= HEAVY_RVOL:
        return "heavy"
    return "elevated"


def _range_note(trend: TrendView) -> str:
    position = trend.pct_of_52wk_range
    if position is None:
        return ""
    if position >= 1 - RANGE_EDGE:
        return " at 52-week highs"
    if position <= RANGE_EDGE:
        return " at 52-week lows"
    return ""


def classify(
    stats: VolumeStats, trend: Optional[TrendView], change_pct: Optional[float]
) -> Insight:
    """Name the pattern in ``stats`` given the stock's trend and day move."""
    volume_phrase = f"{stats.multiple_label} normal volume"
    if trend is None:
        return Insight(
            label="Unusual volume",
            note=f"{volume_phrase}; no trend history to read it against.",
        )

    edge = _range_note(trend)
    scale = intensity(stats.rvol)

    if change_pct is None or abs(change_pct) < DIRECTIONAL_MOVE:
        return Insight(
            label="Volume without direction",
            note=(
                f"{scale.capitalize()} participation ({volume_phrase}) with the "
                f"price barely moved{edge} — churn, rotation, or positioning "
                "ahead of news."
            ),
        )

    buying = change_pct > 0
    move = f"{change_pct * 100:+.1f}%"

    if buying:
        if trend.long_term == UP:
            if trend.short_term == UP:
                return Insight(
                    label="Trend continuation",
                    note=(
                        f"{move} on {volume_phrase}{edge}, with both horizons "
                        "already pointing up — buyers pressing an established "
                        "uptrend."
                    ),
                    direction=BULLISH,
                )
            return Insight(
                label="Pullback bought",
                note=(
                    f"{move} on {volume_phrase} after a soft stretch, inside a "
                    f"long-term uptrend{edge} — the dip is being absorbed."
                ),
                direction=BULLISH,
            )
        if trend.long_term == DOWN:
            return Insight(
                label="Counter-trend rally",
                note=(
                    f"{move} on {volume_phrase}{edge} against a long-term "
                    "downtrend — a bounce or a squeeze until the 200-day says "
                    "otherwise."
                ),
                direction=NEUTRAL,
            )
        return Insight(
            label="Breakout attempt",
            note=(
                f"{move} on {volume_phrase}{edge} out of a directionless "
                "long-term base — the kind of volume that starts trends."
            ),
            direction=BULLISH,
        )

    if trend.long_term == UP:
        if trend.short_term == UP:
            return Insight(
                label="Distribution into strength",
                note=(
                    f"{move} on {volume_phrase}{edge} while both horizons still "
                    "read up — heavy supply arriving into a rally."
                ),
                direction=BEARISH,
            )
        return Insight(
            label="Uptrend under pressure",
            note=(
                f"{move} on {volume_phrase}{edge}; the long-term uptrend holds "
                "but short-term momentum has already rolled over."
            ),
            direction=BEARISH,
        )
    if trend.long_term == DOWN:
        return Insight(
            label="Downtrend acceleration",
            note=(
                f"{move} on {volume_phrase}{edge} with both horizons down — "
                f"sellers pressing, and {scale} volume on new lows is where "
                "capitulation shows up."
            ),
            direction=BEARISH,
        )
    return Insight(
        label="Breakdown attempt",
        note=(
            f"{move} on {volume_phrase}{edge} out of a directionless long-term "
            "base — supply taking control of the range."
        ),
        direction=BEARISH,
    )
