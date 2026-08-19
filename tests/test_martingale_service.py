import datetime as dt
import json
import os

import pytest

import martingale_trader.service as service_mod
from earnings_analyzer.models import PriceBar
from martingale_trader.config import MartingaleConfig
from martingale_trader.service import run_martingale

D1 = dt.date(2026, 8, 17)
D2 = dt.date(2026, 8, 18)


class RecordingNotifier:
    sent = []

    def __init__(self, config):
        self.config = config

    def send(self, subject, text, html):
        if not self.config.dry_run:
            RecordingNotifier.sent.append(subject)


class FakeProvider:
    def __init__(self, closes):
        self._bars = [
            PriceBar(day=day, open=c, high=c, low=c, close=c)
            for day, c in closes
        ]

    def price_history(self, ticker, days=700):
        return self._bars


def _config(tmp_path, dry_run=False, **kw):
    return MartingaleConfig(
        dry_run=dry_run,
        smtp_host="smtp.example.com",
        email_from="a@example.com",
        email_to=["a@example.com"],
        martingale_state_file=str(tmp_path / "martingale.json"),
        **kw,
    )


def _patch_notifier(monkeypatch):
    RecordingNotifier.sent = []
    monkeypatch.setattr(service_mod, "EmailNotifier", RecordingNotifier)


def _state(config):
    with open(config.martingale_state_file, encoding="utf-8") as fh:
        return json.load(fh)


def test_first_run_opens_round_and_emails(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    result = run_martingale(
        config, today=D1, provider=FakeProvider([(D1, 6400.0)])
    )
    assert (result.settled, result.opened, result.emails_sent) == (0, 1, 1)
    assert "first round opened" in result.subject
    state = _state(config)
    assert state["open_round"]["entry_price"] == 6400.0
    # Champion sizing: 30% of the $25,000 balance at step 0.
    assert state["open_round"]["notional"] == 7500.0
    assert state["balance"] == 25000.0


def test_duplicate_run_same_day_skips(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    provider = FakeProvider([(D1, 6400.0)])
    run_martingale(config, today=D1, provider=provider)
    result = run_martingale(config, today=D1, provider=provider)
    assert result.skipped is True
    assert len(RecordingNotifier.sent) == 1


def test_next_day_settles_loss_and_doubles(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    run_martingale(config, today=D1, provider=FakeProvider([(D1, 6400.0)]))
    result = run_martingale(
        config, today=D2,
        provider=FakeProvider([(D1, 6400.0), (D2, 6336.0)]),   # -1%
    )
    assert (result.settled, result.opened) == (1, 1)
    state = _state(config)
    # -1% on the $7,500 step-0 stake.
    assert state["balance"] == 24925.0
    assert state["step"] == 1
    # Next stake: 30% of $24,925, tripled once.
    assert state["open_round"]["notional"] == 22432.5
    assert state["rounds"][0]["pnl"] == -75.0


def test_bust_stops_trading(tmp_path, monkeypatch):
    # Fixed-dollar mode (base_pct=0): the $5k stake can outsize the
    # $100 account, so one -2% day busts it.
    _patch_notifier(monkeypatch)
    config = _config(tmp_path, martingale_start_balance=100.0,
                     martingale_base_pct=0.0)
    run_martingale(config, today=D1, provider=FakeProvider([(D1, 6400.0)]))
    result = run_martingale(
        config, today=D2,
        provider=FakeProvider([(D1, 6400.0), (D2, 6272.0)]),   # -2% = -$100
    )
    assert (result.settled, result.opened) == (1, 0)
    assert "BUSTED" in result.subject
    state = _state(config)
    assert state["busted"] is True
    assert state["open_round"] is None
    # Later runs do nothing and send nothing.
    later = run_martingale(
        config, today=D2 + dt.timedelta(days=1),
        provider=FakeProvider([(D2 + dt.timedelta(days=1), 6000.0)]),
    )
    assert later.skipped is True
    assert len(RecordingNotifier.sent) == 2


def test_dry_run_saves_nothing(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path, dry_run=True)
    result = run_martingale(
        config, today=D1, provider=FakeProvider([(D1, 6400.0)])
    )
    assert result.emails_sent == 0
    assert not os.path.exists(config.martingale_state_file)
    assert RecordingNotifier.sent == []


def test_future_bars_are_ignored(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    run_martingale(
        config, today=D1,
        provider=FakeProvider([(D1, 6400.0), (D2, 9999.0)]),
    )
    assert _state(config)["open_round"]["entry_price"] == 6400.0


def test_no_bars_raises(tmp_path, monkeypatch):
    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    with pytest.raises(RuntimeError):
        run_martingale(config, today=D1, provider=FakeProvider([]))


def test_default_config_is_the_champion():
    # 30% of balance, factor 3, $160k table limit.
    from martingale_trader.engine import notional_for_step

    config = MartingaleConfig()
    assert config.martingale_base_pct == 0.30
    assert config.martingale_factor == 3.0
    assert config.martingale_max_notional == 160000.0
    assert notional_for_step(
        99, 7500.0, config.martingale_factor, config.martingale_max_notional
    ) == 160000.0


def test_missing_schwab_credentials_abort_the_run(tmp_path, monkeypatch):
    # Schwab only, no Yahoo fallback: without credentials the run fails
    # before it touches any market data.
    from earnings_notifier.config import ConfigError

    _patch_notifier(monkeypatch)
    config = _config(tmp_path)
    with pytest.raises(ConfigError, match="SCHWAB_APP_KEY"):
        run_martingale(config, today=D1)


def test_schwab_history_requests_spx_daily_bars(monkeypatch):
    from martingale_trader.service import SchwabHistory

    calls = []

    class FakeSession:
        def get(self, path, params):
            calls.append((path, params))
            return {"candles": [
                {"datetime": 1755475200000, "open": 6400.0, "high": 6410.0,
                 "low": 6390.0, "close": 6405.0, "volume": 1.0},
            ]}

    bars = SchwabHistory(FakeSession()).price_history("$SPX")
    assert calls[0][0] == "/pricehistory"
    assert calls[0][1]["symbol"] == "$SPX"
    assert calls[0][1]["frequencyType"] == "daily"
    assert calls[1][1]["frequencyType"] == "minute"
    assert len(bars) == 1 and bars[0].close == 6405.0


def _intraday_candles(day_utc, hours, base=6400.0):
    """30-minute candles starting 13:30 UTC (9:30 ET) for `hours` hours."""
    start = dt.datetime(day_utc.year, day_utc.month, day_utc.day, 13, 30,
                        tzinfo=dt.timezone.utc)
    out = []
    for i in range(int(hours * 2) + 1):
        ts = start + dt.timedelta(minutes=30 * i)
        out.append({"datetime": int(ts.timestamp() * 1000),
                    "open": base + i, "high": base + i + 2,
                    "low": base + i - 2, "close": base + i + 1,
                    "volume": 10.0})
    return out


def test_intraday_synthesizes_completed_session():
    from martingale_trader.service import daily_bar_from_intraday

    full = {"candles": _intraday_candles(D2, 6.0)}      # 9:30-15:30 ET
    bar = daily_bar_from_intraday(full)
    assert bar is not None
    assert bar.day == D2
    assert bar.close == full["candles"][-1]["close"]
    assert bar.open == full["candles"][0]["open"]
    assert bar.high == max(c["high"] for c in full["candles"])

    partial = {"candles": _intraday_candles(D2, 3.0)}   # mid-session
    assert daily_bar_from_intraday(partial) is None
    assert daily_bar_from_intraday({"candles": []}) is None
    assert daily_bar_from_intraday(None) is None

    # Newest day partial -> fall back to the older complete session.
    two_days = {"candles": _intraday_candles(D1, 6.0)
                + _intraday_candles(D2, 2.0)}
    bar = daily_bar_from_intraday(two_days)
    assert bar is not None and bar.day == D1


def test_schwab_history_appends_intraday_close(monkeypatch):
    # Daily feed still shows D1; the completed D2 session fills the gap.
    from martingale_trader.service import SchwabHistory

    class FakeSession:
        def get(self, path, params):
            if params["frequencyType"] == "daily":
                return {"candles": [
                    {"datetime": int(dt.datetime(
                        D1.year, D1.month, D1.day, 5,
                        tzinfo=dt.timezone.utc).timestamp() * 1000),
                     "open": 6400.0, "high": 6410.0, "low": 6390.0,
                     "close": 6400.0, "volume": 1.0}]}
            return {"candles": _intraday_candles(D2, 6.0)}

    bars = SchwabHistory(FakeSession()).price_history("$SPX")
    assert [b.day for b in bars] == [D1, D2]
    assert bars[-1].close == 6413.0     # last 30-min candle's close


def _ms(*args):
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_bar_from_quote_close_time_guard():
    from martingale_trader.service import bar_from_quote

    def quote(ms):
        return {"lastPrice": 7691.76, "openPrice": 7740.0,
                "highPrice": 7750.0, "lowPrice": 7680.0,
                "quoteTimeInLong": ms}

    # Summer (EDT): 20:38 UTC = 16:38 ET -> accepted for that day.
    bar = bar_from_quote(quote(_ms(2026, 8, 18, 20, 38)))
    assert bar is not None and bar.day == dt.date(2026, 8, 18)
    assert bar.close == 7691.76 and bar.high == 7750.0
    # Summer: 19:00 UTC = 15:00 ET -> mid-session, rejected.
    assert bar_from_quote(quote(_ms(2026, 8, 18, 19, 0))) is None
    # Winter (EST): 21:38 UTC = 16:38 ET -> accepted.
    assert bar_from_quote(quote(_ms(2026, 1, 15, 21, 38))) is not None
    # Winter: 20:38 UTC = 15:38 ET -> rejected (the DST trap).
    assert bar_from_quote(quote(_ms(2026, 1, 15, 20, 38))) is None
    assert bar_from_quote({}) is None


def test_quote_supplies_same_day_close(monkeypatch):
    # History feeds (daily + intraday) end at D1; the after-close quote
    # carries D2's close and wins.
    from martingale_trader.service import SchwabHistory

    class FakeSession:
        def get(self, path, params):
            if path == "/quotes":
                return {"$SPX": {"quote": {
                    "lastPrice": 7691.76,
                    "quoteTimeInLong": _ms(D2.year, D2.month, D2.day, 20, 38),
                }}}
            if params["frequencyType"] == "daily":
                return {"candles": [
                    {"datetime": _ms(D1.year, D1.month, D1.day, 5),
                     "open": 7740.0, "high": 7750.0, "low": 7700.0,
                     "close": 7745.06, "volume": 1.0}]}
            return {"candles": []}

    bars = SchwabHistory(FakeSession()).price_history("$SPX")
    assert [b.day for b in bars] == [D1, D2]
    assert bars[-1].close == 7691.76


def test_schwab_history_survives_intraday_failure(monkeypatch):
    from martingale_trader.service import SchwabHistory

    class FakeSession:
        def get(self, path, params):
            if params["frequencyType"] == "daily":
                return {"candles": [
                    {"datetime": 1755475200000, "open": 6400.0,
                     "high": 6410.0, "low": 6390.0, "close": 6405.0,
                     "volume": 1.0}]}
            raise RuntimeError("intraday endpoint down")

    bars = SchwabHistory(FakeSession()).price_history("$SPX")
    assert len(bars) == 1 and bars[0].close == 6405.0
