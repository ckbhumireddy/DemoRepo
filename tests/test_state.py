import datetime as dt

from earnings_notifier.earnings import EarningsEvent
from earnings_notifier.state import event_key, load_state, prune, save_state

TODAY = dt.date(2026, 8, 9)


def test_event_key_is_ticker_and_date():
    e = EarningsEvent("NBIS", dt.date(2026, 8, 12), True)
    assert event_key(e) == "NBIS:2026-08-12"


def test_load_missing_file_is_empty(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) == set()


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "sub" / "notified.json")  # nested dir auto-created
    save_state(path, {"NBIS:2026-08-12", "AAPL:2026-08-15"}, TODAY)
    assert load_state(path) == {"NBIS:2026-08-12", "AAPL:2026-08-15"}


def test_prune_drops_old_keys():
    keys = {
        "OLD:2026-01-01",   # far past
        "NEW:2026-08-12",   # recent
        "garbage-key",      # malformed -> dropped
    }
    kept = prune(keys, TODAY, retention_days=30)
    assert kept == {"NEW:2026-08-12"}


def test_save_prunes_old_entries(tmp_path):
    path = str(tmp_path / "notified.json")
    save_state(path, {"OLD:2025-01-01", "NEW:2026-08-12"}, TODAY, retention_days=60)
    assert load_state(path) == {"NEW:2026-08-12"}


def test_corrupt_state_file_starts_empty(tmp_path):
    path = tmp_path / "notified.json"
    path.write_text("{not valid json")
    assert load_state(str(path)) == set()
