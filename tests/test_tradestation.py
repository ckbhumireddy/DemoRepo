import datetime as dt

import pytest

from market_insights.tradestation import (
    LIVE_BASE,
    SIM_BASE,
    Quote,
    TradeStationAuthError,
    TradeStationSession,
    api_base,
    bars_from_payload,
    last_bar_is_open,
    quotes_from_payload,
    tradestation_symbol,
    yahoo_symbol,
)


def _bar(ts, close="100.5", volume="1000", status="Closed"):
    return {
        "TimeStamp": ts,
        "Open": "99.0",
        "High": "101.0",
        "Low": "98.5",
        "Close": close,
        "TotalVolume": volume,
        "BarStatus": status,
    }


# --------------------------------------------------------------------------- #
# Symbols
# --------------------------------------------------------------------------- #
def test_symbol_mapping_roundtrips_share_classes():
    assert tradestation_symbol("BRK-B") == "BRK.B"
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert tradestation_symbol("AAPL") == "AAPL"


def test_api_base_selects_simulation_host():
    assert api_base("sim") == SIM_BASE
    assert api_base("simulation") == SIM_BASE
    assert api_base("live") == LIVE_BASE
    assert api_base("") == LIVE_BASE


# --------------------------------------------------------------------------- #
# Bar parsing
# --------------------------------------------------------------------------- #
def test_bars_parse_stringly_typed_numbers():
    bars = bars_from_payload({"Bars": [_bar("2026-08-21T20:00:00Z")]})
    assert len(bars) == 1
    assert bars[0].close == 100.5
    assert bars[0].volume == 1000.0
    assert bars[0].high == 101.0


def test_bars_are_dated_in_eastern_across_the_dst_switch():
    # 20:00Z in August (EDT) and 21:00Z in December (EST) are both 16:00 ET,
    # so both must land on their own trading day.
    payload = {"Bars": [
        _bar("2026-08-21T20:00:00Z"),
        _bar("2026-12-15T21:00:00Z"),
    ]}
    days = [b.day for b in bars_from_payload(payload)]
    assert days == [dt.date(2026, 8, 21), dt.date(2026, 12, 15)]


def test_bars_drop_unusable_rows_rather_than_zero_filling():
    payload = {"Bars": [
        _bar("2026-08-21T20:00:00Z"),
        _bar("2026-08-22T20:00:00Z", close=""),      # empty string
        _bar("2026-08-23T20:00:00Z", close="0"),     # zero close
        {"TimeStamp": "garbage", "Close": "10"},     # unparseable date
        {"Close": "10"},                             # no timestamp
    ]}
    bars = bars_from_payload(payload)
    assert [b.day for b in bars] == [dt.date(2026, 8, 21)]


def test_bars_come_back_sorted_and_empty_payloads_are_safe():
    payload = {"Bars": [
        _bar("2026-08-23T20:00:00Z"),
        _bar("2026-08-21T20:00:00Z"),
    ]}
    assert [b.day.day for b in bars_from_payload(payload)] == [21, 23]
    assert bars_from_payload({}) == []
    assert bars_from_payload({"Bars": None}) == []


def test_last_bar_is_open_reads_bar_status():
    closed = {"Bars": [_bar("2026-08-21T20:00:00Z")]}
    live = {"Bars": [_bar("2026-08-21T20:00:00Z"),
                     _bar("2026-08-24T14:00:00Z", status="Open")]}
    assert last_bar_is_open(closed) is False
    assert last_bar_is_open(live) is True
    assert last_bar_is_open({"Bars": []}) is False


# --------------------------------------------------------------------------- #
# Quote parsing
# --------------------------------------------------------------------------- #
def test_quotes_key_on_yahoo_style_tickers():
    payload = {"Quotes": [
        {"Symbol": "BRK.B", "Last": "450.10", "Volume": "3000000",
         "PreviousVolume": "1000000", "PreviousClose": "440.00",
         "NetChangePct": "2.29", "High52Week": "460", "Low52Week": "380"},
    ]}
    quotes = quotes_from_payload(payload)
    assert set(quotes) == {"BRK-B"}
    quote = quotes["BRK-B"]
    assert quote.symbol == "BRK.B"          # the feed's own spelling is kept
    assert quote.volume == 3_000_000
    assert quote.previous_volume == 1_000_000
    assert quote.change_pct == pytest.approx(0.0229)


def test_quote_change_pct_falls_back_to_previous_close():
    quote = Quote(symbol="X", last=110.0, previous_close=100.0)
    assert quote.change_pct == pytest.approx(0.10)
    assert Quote(symbol="X").change_pct is None


def test_quotes_skip_unnamed_rows_and_tolerate_errors_block():
    payload = {"Quotes": [{"Last": "1"}], "Errors": [{"Symbol": "NOPE"}]}
    assert quotes_from_payload(payload) == {}


# --------------------------------------------------------------------------- #
# Session token handling
# --------------------------------------------------------------------------- #
def test_session_accepts_json_token_and_a_bare_refresh_token():
    session = TradeStationSession(
        "id", "secret", token_json='{"refresh_token": "abc", "expires_at": 0}'
    )
    assert session._token["refresh_token"] == "abc"

    bare = TradeStationSession("id", "secret", token_json="just-a-refresh-token")
    assert bare._token["refresh_token"] == "just-a-refresh-token"


def test_session_reads_a_token_file(tmp_path):
    path = tmp_path / "token.json"
    path.write_text('{"refresh_token": "from-file"}', encoding="utf-8")
    session = TradeStationSession("id", "secret", token_file=str(path))
    assert session._token["refresh_token"] == "from-file"


def test_session_rejects_unusable_configuration():
    with pytest.raises(TradeStationAuthError, match="CLIENT_ID"):
        TradeStationSession("", "secret", token_json='{"refresh_token": "a"}')
    with pytest.raises(TradeStationAuthError, match="no TradeStation token"):
        TradeStationSession("id", "secret")
    with pytest.raises(TradeStationAuthError, match="not valid JSON"):
        TradeStationSession("id", "secret", token_json="{nope")
    with pytest.raises(TradeStationAuthError, match="no refresh_token"):
        TradeStationSession("id", "secret", token_json='{"access_token": "a"}')


def test_session_reuses_an_unexpired_access_token():
    import time

    session = TradeStationSession(
        "id", "secret",
        token_json='{"refresh_token": "r", "access_token": "still-good",'
                   f' "expires_at": {int(time.time()) + 600}}}',
    )
    # No network call: a valid token short-circuits the refresh.
    assert session._access_token() == "still-good"
