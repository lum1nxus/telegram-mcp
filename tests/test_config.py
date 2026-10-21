import json
import sys
from pathlib import Path

import pytest

from tgmcp import config


def test_env_overrides_file(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    env = {"TGMCP_HOME": str(tmp_path), "TGMCP_API_ID": "2", "TGMCP_HIDDEN_CHATS": "@y, -100123"}
    s = config.load_settings(env)
    assert (s.api_id, s.api_hash, s.hidden_chats) == (2, "a", ("@y", "-100123"))
    assert s.session_path == tmp_path / "telegram.session"
    assert s.log_path == tmp_path / "tgmcp.log"


def test_missing_credentials_is_config_error(tmp_path):
    with pytest.raises(config.ConfigError, match="api_id"):
        config.load_settings({"TGMCP_HOME": str(tmp_path)})


def test_save_credentials_keeps_hidden_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "restrict_to_owner", lambda p: None)
    (tmp_path / "config.json").write_text(
        '{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    config.save_credentials(5, "h", tmp_path)
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {
        "api_id": 5,
        "api_hash": "h",
        "hidden_chats": ["@x"],
        "expected_user": None,
    }


@pytest.mark.parametrize("value", ["", "  "])
def test_empty_hidden_chats_env_is_unset(tmp_path, value):
    (tmp_path / "config.json").write_text(
        '{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    env = {"TGMCP_HOME": str(tmp_path), "TGMCP_HIDDEN_CHATS": value}
    assert config.load_settings(env).hidden_chats == ("@x",)


def test_ensure_home_restricts_only_a_new_folder(tmp_path, monkeypatch):
    restricted: list[Path] = []
    monkeypatch.setattr(config, "restrict_to_owner", restricted.append)
    home = tmp_path / "a" / "telegram-mcp"
    assert config.ensure_home(home) == home
    assert home.is_dir()
    assert config.ensure_home(home) == home
    assert restricted == [home]


def test_save_credentials_restricts_a_new_home(tmp_path, monkeypatch):
    restricted: list[Path] = []
    monkeypatch.setattr(config, "restrict_to_owner", restricted.append)
    home = tmp_path / "new"
    config.save_credentials(5, "h", home)
    assert restricted == [home]


def test_forget_credentials_keeps_other_settings(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8")
    config.forget_credentials(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"hidden_chats": ["@x"]}
    config.forget_credentials(tmp_path / "missing")  # no file: nothing to do


@pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
@pytest.mark.parametrize("username", ["from-env", ""])
def test_restrict_to_owner_keeps_the_owner_able_to_read(tmp_path, monkeypatch, username):
    if not username:
        monkeypatch.setenv("USERNAME", "")  # falls back to `whoami`
    folder = tmp_path / "home"
    folder.mkdir()
    config.restrict_to_owner(folder)
    secret = folder / "telegram.session"
    secret.write_text("key", encoding="utf-8")
    config.restrict_to_owner(secret)
    assert secret.read_text(encoding="utf-8") == "key"
    assert [p.name for p in folder.iterdir()] == ["telegram.session"]
