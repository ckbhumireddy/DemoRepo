"""The screener: combine quality + crash filters and rank candidates."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from ..config import ScreenConfig
from ..data.models import Candidate
from ..data.provider import MarketDataProvider
from .chain_pricing import build_priced_strategies, select_expiry
from .earnings import detect_earnings_crash
from .fundamentals import score_fundamentals
from .options import suggest_option_strategies
from .volatility import compute_iv_rank


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

    def evaluate(
        self,
        ticker: str,
        *,
        today: Optional[date] = None,
        include_options: bool = False,
    ) -> Optional[Candidate]:
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
        if include_options:
            self.enrich_with_options(candidate, today=today)
        return candidate

    def enrich_with_options(
        self, candidate: Candidate, *, today: Optional[date] = None
    ) -> None:
        """Attach live IV rank and priced strategies to a candidate.

        No-op when the provider can't supply option chains. Failures degrade to
        leaving the fields empty rather than raising.
        """
        provider = self.provider
        if not provider.supports_options():
            return
        ticker = candidate.ticker
        today_eff = today or date.today()

        expiries = provider.get_option_expiries(ticker)
        if not expiries:
            return

        near_expiry = select_expiry(expiries, today_eff, min_dte=25, max_dte=50)
        leaps_candidates = [e for e in expiries if (e - today_eff).days >= 150]
        leaps_expiry = max(leaps_candidates) if leaps_candidates else None

        near_chain = (
            provider.get_option_chain(ticker, near_expiry) if near_expiry else None
        )
        leaps_chain = (
            provider.get_option_chain(ticker, leaps_expiry)
            if leaps_expiry and leaps_expiry != near_expiry
            else None
        )

        current_iv = near_chain.atm_iv() if near_chain is not None else None
        vol_prices = provider.get_price_history(ticker, days=400)
        candidate.iv_rank = compute_iv_rank(ticker, current_iv, vol_prices)

        candidate.priced_strategies = build_priced_strategies(
            near_chain,
            leaps_chain,
            fallback_spot=candidate.crash.current_close,
            high_quality=candidate.fundamental_score >= 75,
        )

    def screen(
        self,
        tickers: List[str],
        *,
        today: Optional[date] = None,
        include_options: bool = False,
    ) -> List[Candidate]:
        """Evaluate many tickers and return passing candidates, best first."""
        results: List[Candidate] = []
        for ticker in tickers:
            candidate = self.evaluate(
                ticker, today=today, include_options=include_options
            )
            if candidate is not None:
                results.append(candidate)
        results.sort(key=lambda c: c.composite_score, reverse=True)
        return results
