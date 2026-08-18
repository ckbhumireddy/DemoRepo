import datetime as dt
import os

import iv_scanner.service as service_mod
from earnings_analyzer.models import IVRank
from iv_scanner.config import IVScanConfig
from iv_scanner.emails import render_scan_email
from iv_scanner.plays import suggest_plays
from iv_scanner.scanner import ScanRow, annotate, pick_expiry, scan, select_top
from iv_scanner.service import run_scan

TODAY = dt.date(2026, 8, 18)


def _row(ticker, rank, held=False, earnings=None, timing=None):
    return ScanRow(
        rank=IVRank(ticker=ticker, current_iv=0.5, hv_low=0.2, hv_high=0.6,
                    iv_rank=rank, iv_percentile=rank, window_days=21,
                    sample_size=200),
        price=100.0,
        expiry=TODAY + dt.timedelta(days=30),
        held=held,
        earnings_date=earnings,
        earnings_timing=timing,
    )


# --------------------------------------------------------------------------- #
# Scanner pure parts
# --------------------------------------------------------------------------- #
def test_pick_expiry_prefers_30_dte_and_skips_near():
    expiries = [TODAY + dt.timedelta(days=d) for d in (2, 9, 23, 37, 65)]
    assert pick_expiry(expiries, TODAY) == TODAY + dt.timedelta(days=23)
    assert pick_expiry([TODAY + dt.timedelta(days=2)], TODAY) is None


def test_scan_survives_failures():
    def fetch(ticker):
        if ticker == "BOOM":
            raise RuntimeError("x")
        if ticker == "NONE":
            return None
        return _row(ticker, 0.5)

    rows, failures = scan(["A", "BOOM", "NONE", "B"], fetch, max_workers=2)
    assert sorted(r.ticker for r in rows) == ["A", "B"]
    assert failures == 2


def test_select_top_and_annotate():
    rows = [_row("LOW", 0.2), _row("HI", 0.9), _row("MID", 0.5)]
    top = select_top(rows, 2)
    assert [r.ticker for r in top] == ["HI", "MID"]
    window = {"events": [
        {"ticker": "HI", "date": TODAY + dt.timedelta(days=2),
         "timing": "after-market", "is_estimate": True},
    ]}
    annotate(rows, {"MID"}, window)
    assert next(r for r in rows if r.ticker == "MID").held
    hi = next(r for r in rows if r.ticker == "HI")
    assert hi.earnings_date == TODAY + dt.timedelta(days=2)
    assert hi.earnings_timing == "after-market"


# --------------------------------------------------------------------------- #
# Plays
# --------------------------------------------------------------------------- #
def test_play_rules():
    rows = [
        _row("HELD", 0.85, held=True),
        _row("EVENT", 0.9, earnings=TODAY + dt.timedelta(days=2), timing="pre-market"),
        _row("RICH", 0.85),
        _row("CHEAPHELD", 0.1, held=True),
        _row("NOTHING", 0.5),
    ]
    ideas = suggest_plays(rows)
    by_ticker = {}
    for i in ideas:
        by_ticker.setdefault(i.ticker, []).append(i.idea)
    assert any("Covered calls" in x for x in by_ticker["HELD"])
    assert any("Earnings premium" in x for x in by_ticker["EVENT"])
    assert any("Premium-selling" in x for x in by_ticker["RICH"])
    protection = [i for i in ideas if i.idea.startswith("Cheap protection")]
    assert len(protection) == 1 and "CHEAPHELD 10" in protection[0].why
    assert "NOTHING" not in by_ticker


def test_protection_is_consolidated_and_value_gated():
    rows = [_row(f"P{i}", 0.1, held=True) for i in range(12)]
    rows.append(_row("TINY", 0.1, held=True))
    ideas = suggest_plays(rows, {f"P{i}": 100 for i in range(12)} | {"TINY": 1})
    protection = [i for i in ideas if "protection" in i.idea]
    assert len(protection) == 1            # one line, not twelve
    assert "TINY" not in protection[0].why  # $100 position not worth hedging


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
def test_render_scan_email():
    top = [_row("HI", 0.9, held=True,
                earnings=TODAY + dt.timedelta(days=1), timing="after-market"),
           _row("MID", 0.5)]
    ideas = suggest_plays(top)
    subject, text, html = render_scan_email(
        top, ideas, TODAY, universe_note="2 of 3 tickers ranked"
    )
    assert subject.startswith("[IV] Top 2 IV ranks")
    assert "1 in your portfolio" in subject
    assert "HI" in text and "90" in text
    assert "* = in your portfolio" in text
    assert "Wed Aug 19 (after-market)" in text
    assert "HELD" in html and "<table" in html
    assert "not investment advice" in text.lower()


def test_render_scan_note():
    _, text, html = render_scan_email(
        [_row("A", 0.5)], [], TODAY, scan_note="Schwab unavailable — degraded"
    )
    assert "degraded" in text and "degraded" in html


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


def _config(tmp_path, dry_run=False, **kw):
    kw.setdefault("ivscan_state_file", str(tmp_path / "ivscan.json"))
    return IVScanConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        **kw,
    )


def _patch(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def _run(cfg):
    return run_scan(
        cfg, today=TODAY,
        tickers=["HI", "LOW"],
        fetch=lambda t: _row(t, 0.9 if t == "HI" else 0.2),
        window={"events": []},
        held={"LOW": 200.0},
    )


def test_scan_sends_once_per_day(tmp_path, monkeypatch):
    _patch(monkeypatch)
    cfg = _config(tmp_path)
    first = _run(cfg)
    second = _run(cfg)
    assert first.emails_sent == 1 and first.ranked == 2
    assert second.emails_sent == 0
    assert len(RecordingNotifier.sent) == 1
    assert RecordingNotifier.sent[0].startswith("[IV] Top")


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    _patch(monkeypatch)
    result = _run(_config(tmp_path, dry_run=True))
    assert result.emails_sent == 0
    assert not os.path.exists(str(tmp_path / "ivscan.json"))
    assert RecordingNotifier.sent == []


def test_low_rank_holding_gets_protection_idea(tmp_path, monkeypatch):
    """A held name outside the top list still surfaces as cheap protection."""
    _patch(monkeypatch)
    cfg = _config(tmp_path, ivscan_top_n=1)
    result = _run(cfg)
    assert result.top == 1          # only HI makes the table
    assert result.ideas >= 1        # LOW (held, rank 20) -> cheap protection


def test_config_defaults():
    import unittest.mock as mock

    with mock.patch.dict(os.environ, {}, clear=True):
        cfg = IVScanConfig.from_env()
    assert cfg.ivscan_top_n == 50
    assert cfg.ivscan_universe == "sp500"
    assert cfg.ivscan_state_file == "state/ivscan.json"
    assert cfg.window_file == "out/window.json"
