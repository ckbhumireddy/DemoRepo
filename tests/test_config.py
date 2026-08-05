import pytest

from earnings_notifier.config import Config, ConfigError, _get_list


def test_dry_run_skips_email_validation():
    cfg = Config(dry_run=True)
    cfg.validate()  # should not raise despite missing SMTP config


def test_missing_email_config_raises():
    cfg = Config(dry_run=False)
    with pytest.raises(ConfigError) as exc:
        cfg.validate()
    assert "SMTP_HOST" in str(exc.value)


def test_valid_email_config_passes():
    cfg = Config(
        smtp_host="smtp.example.com",
        email_from="bot@example.com",
        email_to=["me@example.com"],
    )
    cfg.validate()


def test_negative_lead_days_rejected():
    cfg = Config(dry_run=True, lead_days=-1)
    with pytest.raises(ConfigError):
        cfg.validate()


def test_get_list_parses_commas_and_semicolons():
    import os

    os.environ["X_LIST_TEST"] = "a@x.com, b@x.com; c@x.com"
    try:
        assert _get_list("X_LIST_TEST") == ["a@x.com", "b@x.com", "c@x.com"]
    finally:
        del os.environ["X_LIST_TEST"]


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("EMAIL_TO", "a@b.com,c@d.com")
    monkeypatch.setenv("LEAD_DAYS", "5")
    monkeypatch.setenv("SMTP_USE_SSL", "true")
    cfg = Config.from_env()
    assert cfg.smtp_host == "smtp.test"
    assert cfg.smtp_port == 465
    assert cfg.email_to == ["a@b.com", "c@d.com"]
    assert cfg.lead_days == 5
    assert cfg.smtp_use_ssl is True
