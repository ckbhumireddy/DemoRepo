import importlib.util
import os
import pathlib
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "schwab_auth",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "schwab_auth.py",
)
schwab_auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schwab_auth)


def test_load_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "SCHWAB_APP_KEY=abc123\n"
        'SCHWAB_APP_SECRET="s3cret"\n'
        "ALREADY_SET=from-file\n"
        "not a kv line\n",
        encoding="utf-8",
    )
    with mock.patch.dict(os.environ, {"ALREADY_SET": "from-env"}, clear=True):
        assert schwab_auth.load_env_file(str(path)) is True
        assert os.environ["SCHWAB_APP_KEY"] == "abc123"
        assert os.environ["SCHWAB_APP_SECRET"] == "s3cret"   # quotes stripped
        assert os.environ["ALREADY_SET"] == "from-env"       # env wins


def test_load_env_file_missing(tmp_path):
    assert schwab_auth.load_env_file(str(tmp_path / "nope.env")) is False


def test_main_exits_2_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # no .env here
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("sys.argv", ["schwab_auth.py"]):
            assert schwab_auth.main() == 2
