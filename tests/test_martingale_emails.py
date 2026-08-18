import datetime as dt

from martingale_trader.emails import DISCLAIMER, render_daily_email
from martingale_trader.engine import MartingaleSummary

TODAY = dt.date(2026, 8, 18)


def _summary(**kw):
    defaults = dict(
        balance=24950.0, start_balance=25000.0, busted=False,
        step=1, next_notional=10000.0,
    )
    defaults.update(kw)
    return MartingaleSummary(**defaults)


def _settled(pnl=-50.0):
    return {
        "entry_date": "2026-08-17", "entry_price": 6400.0,
        "exit_date": "2026-08-18", "exit_price": 6336.0,
        "notional": 5000.0, "step": 0, "pnl": pnl,
        "return_pct": -1.0, "balance_after": 24950.0,
    }


def _opened():
    return {
        "entry_date": "2026-08-18", "entry_price": 6336.0,
        "notional": 10000.0, "step": 1,
    }


def test_settled_email_has_result_and_ladder():
    summary = _summary(
        rounds_total=1, losses=1, current_loss_streak=1, max_loss_streak=1,
        max_drawdown=50.0, recent=[_settled()],
        open_round=_opened(),
    )
    subject, text, html = render_daily_email(
        summary, _settled(), _opened(), TODAY
    )
    assert "-$50.00" in subject
    assert "step 1" in subject
    assert "$24,950.00" in text
    assert "notional $10,000.00" in text.lower()
    assert "0.4x the balance" in text
    assert DISCLAIMER in text
    assert "-$50.00" in html
    assert "Recent rounds" in html


def test_first_open_email():
    subject, text, _html = render_daily_email(
        _summary(step=0, next_notional=5000.0), None, _opened(), TODAY
    )
    assert "first round opened" in subject
    assert "Account:" in text


def test_busted_email():
    summary = _summary(
        balance=-12.0, busted=True, rounds_total=9, losses=9,
        current_loss_streak=9, max_loss_streak=9,
    )
    subject, text, html = render_daily_email(
        summary, _settled(pnl=-3200.0), None, TODAY
    )
    assert "BUSTED" in subject
    assert "BUSTED" in text
    assert "BUSTED" in html
    assert "Next round" not in text
