"""The screener: combine quality + crash filters and rank candidates."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from ..config import ScreenConfig
from ..data.models import Candidate
from ..data.provider import MarketDataProvider
from .earnings import detect_earnings_crash
from .fundamentals import score_fundamentals
from .options import suggest_option_strategies


def _crash_severity_score(max_drop_pct: float, cfg: ScreenConfig) -> float:
    """Map a crash depth to a 0-100 score.

    A drop exactly at the threshold scores ~50; deeper drops score higher, with
    diminishing returns so a -60% name doesn't dwarf everything else.
    """
    drop = abs(max_drop_pct)
    threshold = cfg.min_crash_pct
    if drop <= threshold:
        return 50.0
    # Each additional threshold-sized step adds points on a decaying scale.
    excess = (drop - threshold) / threshold
    return min(100.0, 50.0 + 50.0 * (1 - 1 / (1 + excess)) * 2)


class Screener:
    def __init__(self, provider: MarketDataProvider, cfg: Optional[ScreenConfig] = None):
        self.provider = provider
        self.cfg = cfg or ScreenConfig()

    def evaluate(self, ticker: str, *, today: Optional[date] = None) -> Optional[Candidate]:
        """Evaluate a single ticker; return a Candidate if it passes, else None."""
        cfg = self.cfg
        ticker = ticker.upper()

        fundamentals = self.provider.get_fundamentals(ticker)
        if fundamentals is None:
            return None

        score, coverage, passed, failed = score_fundamentals(fundamentals, cfg)
        if score < cfg.min_fundamental_score:
            return None

        prices = self.provider.get_price_history(ticker)
        earnings_date = self.provider.get_last_earnings_date(ticker)
        crash = detect_earnings_crash(ticker, prices, earnings_date, cfg, today=today)
        if crash is None:
            return None

        candidate = Candidate(
            ticker=ticker,
            fundamentals=fundamentals,
            crash=crash,
            fundamental_score=score,
            data_coverage=coverage,
            passed_criteria=passed,
            failed_criteria=failed,
        )
        candidate.option_suggestions = suggest_option_strategies(candidate)
        candidate.composite_score = round(
            cfg.quality_weight * score
            + cfg.crash_weight * _crash_severity_score(crash.max_drop_pct, cfg),
            1,
        )
        return candidate

    def screen(
        self, tickers: List[str], *, today: Optional[date] = None
    ) -> List[Candidate]:
        """Evaluate many tickers and return passing candidates, best first."""
        results: List[Candidate] = []
        for ticker in tickers:
            candidate = self.evaluate(ticker, today=today)
            if candidate is not None:
                results.append(candidate)
        results.sort(key=lambda c: c.composite_score, reverse=True)
        return results
