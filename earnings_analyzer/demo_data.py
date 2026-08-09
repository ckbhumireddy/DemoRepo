"""Synthetic, offline data for ``--demo`` runs and the test suite.

Black-Scholes-synthesized option chains plus canned histories build an
:class:`InMemoryProvider` with five scenario tickers, one per branch of the
strategy rule table, so the whole pipeline can run without touching the
network.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import List, Tuple

from .models import OptionChain, OptionContract, PriceBar, QuarterResult, Snapshot
from .provider import InMemoryProvider

_RISK_FREE = 0.045


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_price(kind: str, S: float, K: float, t: float, sigma: float) -> float:
    """Black-Scholes price used only to synthesize realistic demo chains."""
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if kind == "call" else (K - S))
    d1 = (math.log(S / K) + (_RISK_FREE + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if kind == "call":
        return S * _norm_cdf(d1) - K * math.exp(-_RISK_FREE * t) * _norm_cdf(d2)
    return K * math.exp(-_RISK_FREE * t) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _smile_iv(base_iv: float, strike: float, spot: float) -> float:
    """A simple downside-skewed volatility smile."""
    moneyness = strike / spot
    return round(base_iv + 0.15 * (1 - moneyness) + 0.05 * abs(1 - moneyness), 4)


def build_demo_chain(
    ticker: str,
    expiry: dt.date,
    spot: float,
    base_iv: float,
    today: dt.date,
    *,
    open_interest: float = 1500,
    volume: float = 500,
    spread_frac: float = 0.02,
) -> OptionChain:
    """Synthesize a plausible option chain around ``spot`` for offline runs."""
    t = max((expiry - today).days, 1) / 365.0
    step = 5.0 if spot >= 100 else 2.5
    lo = math.floor(spot * 0.75 / step) * step
    hi = math.ceil(spot * 1.25 / step) * step
    calls: List[OptionContract] = []
    puts: List[OptionContract] = []
    k = lo
    while k <= hi + 1e-9:
        iv = _smile_iv(base_iv, k, spot)
        for kind, bucket in (("call", calls), ("put", puts)):
            mid = round(max(0.01, _bs_price(kind, spot, k, t, iv)), 2)
            spread = max(0.05, round(spread_frac * mid, 2))
            bid = round(max(0.0, mid - spread / 2), 2)
            ask = round(mid + spread / 2, 2)
            itm = (k < spot) if kind == "call" else (k > spot)
            bucket.append(
                OptionContract(
                    contract_symbol=f"{ticker}{expiry:%y%m%d}{kind[0].upper()}{int(k * 1000):08d}",
                    option_type=kind,
                    strike=round(k, 2),
                    expiry=expiry,
                    bid=bid,
                    ask=ask,
                    last=mid,
                    implied_volatility=iv,
                    volume=volume,
                    open_interest=open_interest,
                    in_the_money=itm,
                )
            )
        k += step
    return OptionChain(
        ticker=ticker, expiry=expiry, underlying_price=spot, calls=calls, puts=puts
    )


def _price_series(
    start: dt.date, days: int, base: float, drift_pct_per_day: float
) -> List[PriceBar]:
    """A drifting close series with a deterministic wobble (nonzero vol)."""
    bars = []
    for i in range(days):
        price = base * (1 + drift_pct_per_day / 100.0) ** i
        close = price * (1 + 0.012 * math.sin(i * 1.7))
        bars.append(
            PriceBar(
                day=start + dt.timedelta(days=i),
                open=round(close, 2),
                high=round(close * 1.01, 2),
                low=round(close * 0.99, 2),
                close=round(close, 2),
                volume=1_000_000,
            )
        )
    return bars


def _quarters(
    today: dt.date, reactions: List[float], surprises: List[float]
) -> List[QuarterResult]:
    """Synthetic quarterly history, newest first, ~91 days apart."""
    out = []
    for i, (reaction, surprise) in enumerate(zip(reactions, surprises)):
        est = 1.00 + 0.02 * i
        out.append(
            QuarterResult(
                date=today - dt.timedelta(days=91 * (i + 1)),
                eps_estimate=round(est, 2),
                eps_actual=round(est * (1 + surprise / 100.0), 2),
                surprise_pct=surprise,
                reaction_pct=reaction,
            )
        )
    return out


def build_demo_provider(today: dt.date) -> Tuple[InMemoryProvider, List[Tuple[str, dt.date]]]:
    """Return ``(provider, entries)`` covering every strategy branch.

    Scenarios: RICHCO (rich vol, neutral -> iron condor), BULLCO (rich vol,
    uptrend -> put credit spread), CHEAPCO (cheap vol, neutral -> straddle +
    calendar), BEARCO (cheap vol, downtrend -> put debit spread), THINCO
    (illiquid options -> no trade).
    """
    provider = InMemoryProvider()
    start = today - dt.timedelta(days=700)
    event = today + dt.timedelta(days=3)
    event_expiry = event + dt.timedelta(days=1)
    back_expiry = event + dt.timedelta(days=40)
    entries: List[Tuple[str, dt.date]] = []

    def _scenario(
        ticker, company, spot, drift, base_iv, reactions, surprises,
        *, oi=1500.0, vol=500.0, spread=0.02,
    ):
        provider.add(
            ticker,
            quarters=_quarters(today, reactions, surprises),
            bars=_price_series(start, 700, spot / (1 + drift / 100.0) ** 700, drift),
            snapshot=Snapshot(
                company=company, sector="Demo", market_cap=50e9, forward_pe=22.0,
                revenue_growth=0.12, profit_margin=0.18,
                fifty_two_week_low=spot * 0.7, fifty_two_week_high=spot * 1.2,
            ),
        )
        provider.add_option_chain(
            build_demo_chain(ticker, event_expiry, spot, base_iv, today,
                             open_interest=oi, volume=vol, spread_frac=spread)
        )
        provider.add_option_chain(
            build_demo_chain(ticker, back_expiry, spot, base_iv - 0.08, today,
                             open_interest=oi, volume=vol, spread_frac=spread)
        )
        entries.append((ticker, event))

    # Implied move for a ~4-day expiry is roughly 0.8 * iv * sqrt(t) * 100
    # (~ +/-9.4% at iv=1.0). Historical baselines below are chosen so each
    # scenario lands firmly in its intended verdict.
    up = [4.0, 6.0, -3.0, 5.0, 4.5, -2.0, 6.5, 5.0]        # mixed directions
    beats = [4.0, 3.0, 2.5, 5.0, 1.0, 2.0, 3.5, 4.0]        # consistent beats

    # Rich vol (implied ~6%, hist ~3.5%), flat trend -> iron condor.
    _scenario("RICHCO", "Rich Premium Corp.", 100.0, 0.0, 0.62,
              reactions=[3.5, -3.0, 4.0, -3.5, 3.0, -4.0, 3.5, -3.5],
              surprises=up)
    # Rich vol, steady uptrend -> put credit spread.
    _scenario("BULLCO", "Bull Momentum Inc.", 150.0, 0.08, 0.62,
              reactions=[4.0, 3.5, -3.0, 4.5, 3.0, -3.5, 4.0, 3.0],
              surprises=beats)
    # Cheap vol (implied ~3%, hist ~9%), flat trend -> straddle + calendar.
    _scenario("CHEAPCO", "Cheap Vol Industries", 80.0, 0.0, 0.30,
              reactions=[9.0, -8.5, 10.0, -9.5, 8.0, -10.0, 9.5, -8.0],
              surprises=up)
    # Cheap vol, downtrend -> put debit spread.
    _scenario("BEARCO", "Bear Slide Ltd.", 60.0, -0.08, 0.30,
              reactions=[-9.0, -8.0, 8.5, -10.0, -7.5, 9.0, -9.5, -8.0],
              surprises=[-4.0, -3.0, 2.0, -5.0, -1.0, 2.5, -3.5, -4.0])
    # Illiquid options -> analysis shown, but "no trade".
    _scenario("THINCO", "Thin Markets plc", 45.0, 0.0, 0.55,
              reactions=[5.0, -4.0, 4.5, -5.0, 4.0, -4.5, 5.5, -4.0],
              surprises=up, oi=20.0, vol=3.0, spread=0.25)

    return provider, entries
