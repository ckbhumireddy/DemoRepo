import datetime as dt

from earnings_analyzer.models import ImpliedMove, PricedStrategy, TrendContext
from earnings_trader.candidates import Candidate, SkippedCandidate
from earnings_trader.emails import (
    render_close_email,
    render_open_email,
    render_plan_email,
)
from earnings_trader.ledger import TraderSummary
from earnings_trader.pipeline import TradeDecision

TODAY = dt.date(2026, 8, 17)
EVENT = dt.date(2026, 8, 18)


def _position(pnl=612.0):
    return {
        "ticker": "TGT", "event_date": EVENT.isoformat(), "timing": "pre-market",
        "strategy": "Iron condor", "outlook": "neutral", "qty": 13,
        "legs": [
            {"action": "sell", "option_type": "put", "strike": 95.0,
             "expiry": "2026-08-21", "open_price": 1.30},
            {"action": "buy", "option_type": "put", "strike": 90.0,
             "expiry": "2026-08-21", "open_price": 0.40},
        ],
        "net_premium": 1.2, "max_loss": 3.8, "risk_dollars": 4940.0,
        "open_date": TODAY.isoformat(), "open_spot": 100.0,
        "opened_at": "2026-08-17T19:35:00+00:00", "opened_phase": "entry",
        "signals": {"verdict": "rich", "bias": "neutral",
                    "implied_move_pct": 8.0, "ratio": 1.5,
                    "atm_spread_pct": 2.0},
        "status": "closed", "close_date": (EVENT + dt.timedelta(days=1)).isoformat(),
        "closed_at": None, "close_value": -0.08, "close_method": "chain",
        "realized_pnl": pnl,
    }


def _summary(**kw):
    defaults = dict(balance=25612.0, start_balance=25000.0,
                    closed_total=3, wins=2, window_closed=2, window_wins=2,
                    window_pnl=700.0)
    defaults.update(kw)
    return TraderSummary(**defaults)


def _decision():
    return TradeDecision(
        candidate=Candidate("TGT", EVENT, "pre-market", "tomorrow-bmo"),
        action="open",
        spot=100.0,
        implied=ImpliedMove(expiry=dt.date(2026, 8, 21), straddle_debit=8.0,
                            implied_move_pct=8.0,
                            historical_avg_abs_move_pct=4.0,
                            ratio=2.0, verdict="rich"),
        trend=TrendContext(price=100.0, bias="neutral"),
        strategy=PricedStrategy(strategy="Iron condor", outlook="neutral",
                                net_premium=1.2, max_loss=3.8),
    )


def test_plan_email_contents():
    skipped = [SkippedCandidate("MYSTERY", EVENT, None, "unknown report time")]
    subject, text, html = render_plan_email(
        _summary(open_positions=[_position() | {"status": "open"}]),
        [_decision()], skipped, TODAY,
    )
    assert subject.startswith("[Paper] Daily plan")
    assert "1 planned trade(s)" in subject
    assert "Realized P&L +700.00" in text
    assert "2/2 wins (100%)" in text
    assert "TGT — Iron condor" in text
    assert "ratio 2.00, rich" in text
    assert "MYSTERY: unknown report time" in text
    assert "no real orders" in text
    assert "Daily plan" not in html or "Paper trader daily plan" in html


def test_plan_email_stale_window_note():
    _, text, html = render_plan_email(
        _summary(), [], [], TODAY,
        window_note="Window data is from 2026-08-14 (stale).",
    )
    assert "stale" in text and "stale" in html
    assert "No trades planned." in text


def test_open_email_contents():
    pos = _position() | {"status": "open"}
    subject, text, html = render_open_email(pos)
    assert subject == "[Paper] Opened TGT Iron condor x13 (risk $4,940)"
    assert "sell put 95 exp 2026-08-21 @ $1.30" in " ".join(text.split())
    assert "Net credit $1.20/share" in text
    assert "ratio 1.50, rich" in text
    assert "TGT" in html and "risk" in html


def test_close_email_contents():
    subject, text, html = render_close_email(_position(), _summary())
    assert subject.startswith("[Paper] Closed TGT Iron condor x13: +612.00")
    assert "+2.4% of account" in subject
    assert "Realized P&L: +612.00" in text
    assert "valued via chain" in text
    assert "New balance: $25,612.00" in text
    assert "2-1" in text            # lifetime record
    assert "+612.00" in html


def test_close_email_loss_formatting():
    subject, text, _ = render_close_email(
        _position(pnl=-390.0), _summary(balance=24610.0, wins=1)
    )
    assert "-390.00" in subject
    assert "Realized P&L: -390.00" in text
