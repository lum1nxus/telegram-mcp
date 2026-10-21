import json

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
