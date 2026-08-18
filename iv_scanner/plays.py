"""Rule-based options-play ideas from the scan (pure).

Every idea is educational and tied to a stated signal; the email carries a
not-advice footer. Rules:

1. Held + IV rank >= 70: covered calls on the holding — premium is rich.
2. IV rank >= 80 + earnings within the window: event-premium candidate —
   defined-risk short-vol structures around the report (the paper trader
   applies its own gates before actually trading it).
3. IV rank >= 80, no earnings pending: premium-selling candidate (put
   credit spread / iron condor) — rich vol without an obvious catalyst
   deserves a look at WHY before selling it.
4. Held + IV rank <= 30: cheap protection — protective puts or collars on
   the holding are relatively inexpensive (only surfaces when low-rank
   holdings are scanned; the top-N table itself shows high ranks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .scanner import ScanRow

MAX_IDEAS = 10
MIN_PROTECT_VALUE = 2000.0   # protection ideas only for meaningful positions


@dataclass(frozen=True)
class PlayIdea:
    ticker: str
    idea: str
    why: str


def suggest_plays(
    rows: List[ScanRow],
    qty_by_ticker: Optional[Dict[str, float]] = None,
) -> List[PlayIdea]:
    qty_by_ticker = qty_by_ticker or {}
    ideas: List[PlayIdea] = []
    for r in rows:
        rank_pct = r.rank.iv_rank * 100
        qty = qty_by_ticker.get(r.ticker, 100.0 if r.held else 0.0)
        value = (r.price or 0.0) * qty
        if r.held and r.rank.iv_rank >= 0.7 and qty >= 100:
            # A covered call needs a full 100-share lot behind it.
            ideas.append(PlayIdea(
                ticker=r.ticker,
                idea="Covered calls on your position",
                why=f"you hold {qty:.0f} shares and IV rank is {rank_pct:.0f}"
                    " — premium is rich",
            ))
        if r.rank.iv_rank >= 0.8 and r.earnings_date is not None:
            timing = f" ({r.earnings_timing})" if r.earnings_timing else ""
            ideas.append(PlayIdea(
                ticker=r.ticker,
                idea="Earnings premium play (iron condor / credit spread)",
                why=(
                    f"IV rank {rank_pct:.0f} with earnings "
                    f"{r.earnings_date.strftime('%a %b %d')}{timing} — "
                    "elevated event premium, IV crush follows the report"
                ),
            ))
        elif r.rank.iv_rank >= 0.8:
            ideas.append(PlayIdea(
                ticker=r.ticker,
                idea="Premium-selling candidate (put credit spread / condor)",
                why=(
                    f"IV rank {rank_pct:.0f} without a pending report — "
                    "check the news for why vol is bid before selling it"
                ),
            ))
    # Cheap protection: one consolidated line — half a portfolio can
    # qualify at once, and ten identical bullets drown the distinct ideas.
    protectable = [
        r for r in rows
        if r.held
        and r.rank.iv_rank <= 0.3
        and (r.price or 0.0) * qty_by_ticker.get(r.ticker, 100.0)
        >= MIN_PROTECT_VALUE
    ]
    if protectable:
        protectable.sort(key=lambda r: r.rank.iv_rank)
        listing = " · ".join(
            f"{r.ticker} {r.rank.iv_rank * 100:.0f}" for r in protectable[:8]
        )
        if len(protectable) > 8:
            listing += " · …"
        ideas.append(PlayIdea(
            ticker="",
            idea="Cheap protection on your holdings",
            why=(
                f"low IV rank makes protective puts/collars inexpensive on: "
                f"{listing}"
            ),
        ))
    return ideas[:MAX_IDEAS]
