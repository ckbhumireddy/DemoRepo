import datetime as dt

from earnings_analyzer.models import OptionChain, OptionContract, PriceBar, QuarterResult
from earnings_analyzer.provider import InMemoryProvider
from earnings_trader.candidates import Candidate
from earnings_trader.config import TraderConfig
from earnings_trader.pipeline import evaluate_all, evaluate_candidate

TODAY = dt.date(2026, 8, 17)
EVENT = dt.date(2026, 8, 18)
EXPIRY = dt.date(2026, 8, 21)

CANDIDATE = Candidate("T", EVENT, "pre-market", "tomorrow-bmo")


def _chain(spot=100.0, oi=2000.0, volume=800.0, half_spread=0.05):
    """Strikes 70..130 step 5; ATM straddle prices an ~8% implied move."""
    chain = OptionChain(ticker="T", expiry=EXPIRY, underlying_price=spot)
    for strike in range(70, 135, 5):
        for option_type in ("call", "put"):
            intrinsic = max(
                0.0, spot - strike if option_type == "call" else strike - spot
            )
            time_value = max(0.25, 4.0 - 0.5 * abs(strike - spot) / 5.0)
            mid = intrinsic + time_value
            chain_bucket = chain.calls if option_type == "call" else chain.puts
            chain_bucket.append(
                OptionContract(
                    contract_symbol=f"T-{option_type}-{strike}",
                    option_type=option_type,
                    strike=float(strike),
                    expiry=EXPIRY,
                    bid=mid - half_spread,
                    ask=mid + half_spread,
                    implied_volatility=0.55,
                    volume=volume,
                    open_interest=oi,
                )
            )
    return chain


def _quarters(reactions, surprises=None):
    # Alternate beat/miss by default so the streak never flips the bias.
    surprises = surprises or [1.0 if i % 2 == 0 else -1.0
                              for i in range(len(reactions))]
    return [
        QuarterResult(
            date=TODAY - dt.timedelta(days=90 * (i + 1)),
            eps_estimate=1.0,
            eps_actual=None,
            surprise_pct=s,
            reaction_pct=r,
        )
        for i, (r, s) in enumerate(zip(reactions, surprises))
    ]


def _provider(hist_moves, chain=None, surprises=None):
    provider = InMemoryProvider()
    bars = [
        PriceBar(day=TODAY - dt.timedelta(days=i), open=100, high=100,
                 low=100, close=100.0 * (1.004 if i % 2 else 0.996))
        for i in range(40, 0, -1)
    ]
    provider.add("T", quarters=_quarters(hist_moves, surprises), bars=bars)
    provider.add_option_chain(chain or _chain())
    return provider


def _config(**kw):
    return TraderConfig(dry_run=True, **kw)


# Implied move is ~8% (ATM straddle 4.0 + 4.0 over spot 100).
RICH_HISTORY = [4.0, -4.0, 4.0, -4.0]     # avg |move| 4%  -> ratio 2.0
CHEAP_HISTORY = [16.0, -16.0, 16.0, -16.0]  # avg 16% -> ratio 0.5
FAIR_HISTORY = [8.0, -8.0, 8.0, -8.0]     # avg 8%  -> ratio 1.0


def test_rich_neutral_iron_condor():
    d = evaluate_candidate(CANDIDATE, _provider(RICH_HISTORY), _config(), TODAY)
    assert d.action == "open"
    assert d.strategy.strategy == "Iron condor"
    assert d.implied.verdict == "rich"
    assert d.iv_rank is not None and 0.0 <= d.iv_rank.iv_rank <= 1.0


def test_rich_bullish_put_credit_spread():
    # Beat streak >= 3 flips a neutral trend to bullish.
    provider = _provider(RICH_HISTORY, surprises=[2.0, 2.0, 2.0, 2.0])
    d = evaluate_candidate(CANDIDATE, provider, _config(), TODAY)
    assert d.action == "open"
    assert d.strategy.strategy == "Put credit spread"


def test_cheap_bearish_put_debit_spread():
    provider = _provider(CHEAP_HISTORY, surprises=[-2.0, -2.0, -2.0, -2.0])
    d = evaluate_candidate(CANDIDATE, provider, _config(), TODAY)
    assert d.action == "open"
    assert d.strategy.strategy == "Put debit spread"


def test_cheap_neutral_long_straddle():
    d = evaluate_candidate(CANDIDATE, _provider(CHEAP_HISTORY), _config(), TODAY)
    assert d.action == "open"
    assert d.strategy.strategy in ("Long straddle", "Long strangle")


def test_fair_is_skipped_no_edge():
    d = evaluate_candidate(CANDIDATE, _provider(FAIR_HISTORY), _config(), TODAY)
    assert d.action == "skip"
    assert "fair vol — no edge" in d.reasons


def test_illiquid_skipped_with_reasons():
    chain = _chain(oi=5.0, volume=2.0)
    d = evaluate_candidate(
        CANDIDATE, _provider(RICH_HISTORY, chain=chain), _config(), TODAY
    )
    assert d.action == "skip"
    assert any("thin depth" in r for r in d.reasons)


def test_thin_oi_but_heavy_volume_still_trades():
    """The NBIS case: gap day wipes ATM open interest, volume proves depth."""
    chain = _chain(oi=50.0, volume=15000.0)
    d = evaluate_candidate(
        CANDIDATE, _provider(RICH_HISTORY, chain=chain), _config(), TODAY
    )
    assert d.action == "open"


def test_wide_spread_still_fails():
    chain = _chain(oi=50.0, volume=15000.0, half_spread=0.60)  # >8% at ATM
    d = evaluate_candidate(
        CANDIDATE, _provider(RICH_HISTORY, chain=chain), _config(), TODAY
    )
    assert d.action == "skip"
    assert any("spread" in r for r in d.reasons)


def test_no_chain_skipped():
    provider = InMemoryProvider()
    provider.add("T", quarters=_quarters(RICH_HISTORY), bars=[
        PriceBar(day=TODAY, open=100, high=100, low=100, close=100.0)
    ])
    d = evaluate_candidate(CANDIDATE, provider, _config(), TODAY)
    assert d.action == "skip"
    assert "no option chain spanning the event" in d.reasons


def test_diluted_expiry_skipped():
    provider = InMemoryProvider()
    bars = [PriceBar(day=TODAY, open=100, high=100, low=100, close=100.0)]
    provider.add("T", quarters=_quarters(RICH_HISTORY), bars=bars)
    far = _chain()
    far.expiry = EVENT + dt.timedelta(days=30)
    for c in far.calls + far.puts:
        c.expiry = far.expiry
    provider.add_option_chain(far)
    d = evaluate_candidate(CANDIDATE, provider, _config(), TODAY)
    assert d.action == "skip"
    assert "no reliable implied-move comparison" in d.reasons


def test_evaluate_all_survives_a_failing_ticker():
    class _Exploding:
        def __getattr__(self, name):
            def boom(*args, **kwargs):
                raise RuntimeError("boom")
            return boom

    decisions = evaluate_all([CANDIDATE], _Exploding(), _config(), TODAY)
    assert decisions[0].action == "skip"
    assert "evaluation error" in decisions[0].reasons[0]
