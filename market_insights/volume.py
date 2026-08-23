"""Unusual-volume math (pure functions).

"Unusual" is measured two ways, because each fails differently:

*Relative volume* (RVOL) — projected full-session volume divided by the
median of recent sessions. The median, not the mean: one earnings-day spike
inside the lookback would drag a mean baseline up and hide the next spike.

*Log z-score* — how many standard deviations the day sits above its own
recent distribution, computed on log volume because daily volume is roughly
lognormal (it is bounded below by zero and has a long right tail). RVOL says
"3x normal"; the z-score says whether 3x is remarkable *for this stock* —
2x is extraordinary for a mega-cap and routine for a small one.

Both are computed on a *projected* session, not raw shares so far. A scan at
11:00 ET sees roughly a quarter of the day's volume, so comparing it to full
prior sessions would make every stock look quiet. Volume does not accrue
evenly — the open and close are far heavier than midday — so the projection
uses a U-shaped intraday curve rather than a straight line.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass
from typing import List, Optional, Sequence

from earnings_analyzer.models import PriceBar

from .tradestation import EASTERN

# Regular US equity session, Eastern.
MARKET_OPEN = dt.time(9, 30)
MARKET_CLOSE = dt.time(16, 0)
SESSION_MINUTES = 390

# Cumulative share of a typical session's volume by minutes after the open.
# Approximate and deliberately coarse — its only job is to stop an early
# scan from reading every stock as quiet, and a late one as explosive.
_VOLUME_CURVE = (
    (0, 0.00),
    (30, 0.13),    # the opening auction and its aftermath are heavy
    (60, 0.21),
    (90, 0.28),
    (120, 0.35),
    (150, 0.41),
    (180, 0.47),   # midday lull
    (210, 0.53),
    (240, 0.58),
    (270, 0.64),
    (300, 0.70),
    (330, 0.76),
    (360, 0.84),   # the close ramps hard
    (390, 1.00),
)

# Below this the projection is dividing by a rounding error: in the first
# minutes of the session one block trade would read as a 50x day.
MIN_PROJECTION_FRACTION = 0.05

DEFAULT_LOOKBACK = 20      # ~one trading month of baseline
MIN_ZSCORE_SAMPLES = 8     # fewer than this and the stdev is meaningless


def session_fraction(now: Optional[dt.datetime] = None) -> float:
    """Share of the regular session elapsed at ``now``, in [0, 1].

    Returns 1.0 outside market hours, which is exactly what a scan of
    completed sessions wants (no projection at all).
    """
    moment = now or dt.datetime.now(tz=EASTERN)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=EASTERN)
    local = moment.astimezone(EASTERN)
    if local.time() <= MARKET_OPEN:
        return 0.0
    if local.time() >= MARKET_CLOSE:
        return 1.0

    elapsed = (local.hour * 60 + local.minute) - (
        MARKET_OPEN.hour * 60 + MARKET_OPEN.minute
    )
    previous_minute, previous_share = _VOLUME_CURVE[0]
    for minute, share in _VOLUME_CURVE[1:]:
        if elapsed <= minute:
            span = minute - previous_minute
            weight = (elapsed - previous_minute) / span if span else 0.0
            return previous_share + weight * (share - previous_share)
        previous_minute, previous_share = minute, share
    return 1.0


@dataclass
class VolumeStats:
    """One ticker's volume picture for the session being scanned."""

    ticker: str
    volume: float                     # shares traded so far
    projected: float                  # full-session projection
    baseline: float                   # median daily volume over the lookback
    rvol: float                       # projected / baseline
    zscore: Optional[float]           # log-volume z against the lookback
    dollar_volume: float              # projected shares x price
    fraction: float                   # share of the session elapsed
    partial: bool                     # is the session still running?
    sample_size: int

    @property
    def multiple_label(self) -> str:
        return f"{self.rvol:.1f}x"


def baseline_volume(
    bars: Sequence[PriceBar], lookback: int = DEFAULT_LOOKBACK
) -> Optional[float]:
    """Median daily volume over the most recent ``lookback`` sessions."""
    volumes = [b.volume for b in bars[-lookback:] if b.volume and b.volume > 0]
    if not volumes:
        return None
    return statistics.median(volumes)


def volume_zscore(
    bars: Sequence[PriceBar], projected: float, lookback: int = DEFAULT_LOOKBACK
) -> Optional[float]:
    """Standard deviations above the stock's own recent log-volume mean."""
    volumes = [b.volume for b in bars[-lookback:] if b.volume and b.volume > 0]
    if len(volumes) < MIN_ZSCORE_SAMPLES or projected <= 0:
        return None
    logs = [math.log(v) for v in volumes]
    spread = statistics.pstdev(logs)
    if spread <= 0:
        return None
    return (math.log(projected) - statistics.fmean(logs)) / spread


def project_volume(volume: float, fraction: float) -> float:
    """Scale volume-so-far up to a full-session estimate."""
    if fraction >= 1.0:
        return volume
    return volume / max(fraction, MIN_PROJECTION_FRACTION)


def compute_volume_stats(
    ticker: str,
    history: Sequence[PriceBar],
    current_volume: Optional[float],
    price: Optional[float],
    *,
    fraction: float = 1.0,
    partial: bool = False,
    lookback: int = DEFAULT_LOOKBACK,
) -> Optional[VolumeStats]:
    """Assemble :class:`VolumeStats`; None when the inputs can't support it.

    ``history`` must be the *completed prior* sessions only — the caller
    owns deciding which bar is the live one, since that answer comes from
    the feed (TradeStation's BarStatus) rather than from the numbers.
    """
    if not current_volume or current_volume <= 0:
        return None
    base = baseline_volume(history, lookback)
    if not base or base <= 0:
        return None

    projected = project_volume(current_volume, fraction if partial else 1.0)
    return VolumeStats(
        ticker=ticker,
        volume=current_volume,
        projected=projected,
        baseline=base,
        rvol=projected / base,
        zscore=volume_zscore(history, projected, lookback),
        dollar_volume=projected * (price or 0.0),
        fraction=fraction if partial else 1.0,
        partial=partial,
        sample_size=len([b for b in history[-lookback:] if b.volume]),
    )


def split_current_bar(
    bars: Sequence[PriceBar], partial: bool
) -> tuple[List[PriceBar], Optional[PriceBar]]:
    """Separate the live/most-recent bar from the completed history.

    With ``partial`` the feed has told us the last bar is still forming.
    Without it the last bar is a finished session — still the one being
    scanned, but it needs no projection.
    """
    ordered = sorted(bars, key=lambda b: b.day)
    if not ordered:
        return [], None
    return list(ordered[:-1]), ordered[-1]
