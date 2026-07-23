"""Yahoo Finance provider built on the ``yfinance`` library.

``yfinance`` is imported lazily inside the constructor so the rest of the
package (and the test suite) works even when the dependency isn't installed.
Yahoo data is free and unofficial: it can be rate-limited, occasionally stale,
and fields may be missing -- the analysis layer is built to tolerate that.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional

from .models import Fundamentals, PriceBar
from .provider import MarketDataProvider


def _to_float(value) -> Optional[float]:
    """Best-effort conversion that turns NaN/None/bad values into ``None``."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN != NaN
    if f != f:
        return None
    return f


class YahooProvider(MarketDataProvider):
    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "YahooProvider requires the 'yfinance' package. "
                "Install it with:  pip install yfinance"
            ) from exc
        self._yf = yfinance

    def _ticker(self, ticker: str):
        return self._yf.Ticker(ticker)

    def get_fundamentals(self, ticker: str) -> Optional[Fundamentals]:
        try:
            info = self._ticker(ticker).info or {}
        except Exception:  # pragma: no cover - network dependent
            return None
        if not info:
            return None

        return Fundamentals(
            ticker=ticker.upper(),
            name=info.get("shortName") or info.get("longName"),
            sector=info.get("sector"),
            price=_to_float(
                info.get("currentPrice") or info.get("regularMarketPrice")
            ),
            market_cap=_to_float(info.get("marketCap")),
            trailing_pe=_to_float(info.get("trailingPE")),
            forward_pe=_to_float(info.get("forwardPE")),
            peg_ratio=_to_float(info.get("pegRatio")),
            profit_margin=_to_float(info.get("profitMargins")),
            operating_margin=_to_float(info.get("operatingMargins")),
            return_on_equity=_to_float(info.get("returnOnEquity")),
            free_cash_flow=_to_float(info.get("freeCashflow")),
            revenue_growth=_to_float(info.get("revenueGrowth")),
            earnings_growth=_to_float(info.get("earningsGrowth")),
            debt_to_equity=_normalize_de(_to_float(info.get("debtToEquity"))),
            current_ratio=_to_float(info.get("currentRatio")),
            total_cash=_to_float(info.get("totalCash")),
            total_debt=_to_float(info.get("totalDebt")),
            dividend_yield=_to_float(info.get("dividendYield")),
        )

    def get_price_history(self, ticker: str, days: int = 120) -> List[PriceBar]:
        try:
            hist = self._ticker(ticker).history(
                period=f"{max(days, 30)}d", auto_adjust=False
            )
        except Exception:  # pragma: no cover - network dependent
            return []
        bars: List[PriceBar] = []
        for idx, row in hist.iterrows():
            day = idx.date() if hasattr(idx, "date") else idx
            bars.append(
                PriceBar(
                    day=day,
                    open=_to_float(row.get("Open")) or 0.0,
                    high=_to_float(row.get("High")) or 0.0,
                    low=_to_float(row.get("Low")) or 0.0,
                    close=_to_float(row.get("Close")) or 0.0,
                    volume=_to_float(row.get("Volume")) or 0.0,
                )
            )
        return bars

    def get_last_earnings_date(self, ticker: str) -> Optional[date]:
        tk = self._ticker(ticker)

        # Preferred: the earnings_dates frame (past + upcoming).
        try:
            frame = tk.get_earnings_dates(limit=12)
        except Exception:  # pragma: no cover - network dependent
            frame = None

        today = datetime.utcnow().date()
        if frame is not None and len(frame) > 0:
            past = [
                (idx.date() if hasattr(idx, "date") else idx)
                for idx in frame.index
            ]
            past = [d for d in past if d <= today]
            if past:
                return max(past)

        # Fallback: the calendar dict.
        try:
            cal = tk.calendar
        except Exception:  # pragma: no cover - network dependent
            cal = None
        if isinstance(cal, dict):
            value = cal.get("Earnings Date")
            if isinstance(value, list) and value:
                value = value[0]
            if isinstance(value, (date, datetime)):
                d = value.date() if isinstance(value, datetime) else value
                if d <= today:
                    return d
        return None


def _normalize_de(value: Optional[float]) -> Optional[float]:
    """Yahoo reports debt/equity as a percentage (e.g. 50.0 for 0.5x)."""
    if value is None:
        return None
    return value / 100.0 if value > 5 else value
