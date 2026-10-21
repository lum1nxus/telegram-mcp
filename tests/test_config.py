import json
import subprocess
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
    assert (s.api_id, s.api_hash, s.hidden_chats) == (2, None, ("@y", "-100123"))
    assert s.secrets_path == tmp_path / "secrets.bin"
    assert s.legacy_session_path == tmp_path / "telegram.session"
    assert s.log_template == tmp_path / "tgmcp.log"
    assert config.load_settings({**env, "TGMCP_API_HASH": "dev"}).api_hash == "dev"


@pytest.mark.parametrize("value", ['{"x": 1}', "5"])
def test_hidden_chats_must_be_a_list(tmp_path, value):
    (tmp_path / "config.json").write_text(
        '{"api_id": 1, "hidden_chats": ' + value + "}", encoding="utf-8"
    )
    with pytest.raises(config.ConfigError, match="hidden_chats must be a list"):
        config.load_settings({"TGMCP_HOME": str(tmp_path)})


def test_missing_credentials_is_config_error(tmp_path):
    with pytest.raises(config.ConfigError, match="api_id"):
        config.load_settings({"TGMCP_HOME": str(tmp_path)})


def test_save_credentials_keeps_hidden_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "restrict_to_owner", lambda p: None)
    (tmp_path / "config.json").write_text(
        '{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    config.save_credentials(5, tmp_path)
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == {
        "api_id": 5,
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
    config.save_credentials(5, home)
    assert restricted == [home]


def test_forget_credentials_keeps_other_settings(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8")
    (tmp_path / "secrets.bin").write_bytes(b"blob")
    config.forget_credentials(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"hidden_chats": ["@x"]}
    assert not (tmp_path / "secrets.bin").exists()
    config.forget_credentials(tmp_path / "missing")  # no file: nothing to do


def _aces(path: Path) -> list[str]:
    """The access entries icacls lists for `path`, one per line."""
    shown = subprocess.run(
        [config._system_tool("icacls.exe"), str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    body = shown.replace(str(path), "", 1)
    return [line.strip() for line in body.splitlines() if ":(" in line]


@pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
@pytest.mark.parametrize("username", ["somebody-else", ""])
def test_restrict_to_owner_leaves_exactly_the_owner(tmp_path, monkeypatch, username):
    monkeypatch.setenv("USERNAME", username)  # the grant uses the SID, not this name
    folder = tmp_path / "home"
    folder.mkdir()
    config.restrict_to_owner(folder)
    secret = folder / "secrets.bin"
    secret.write_text("key", encoding="utf-8")
    config.restrict_to_owner(secret)
    assert secret.read_text(encoding="utf-8") == "key"
    for path in (folder, secret):
        aces = _aces(path)
        assert len(aces) == 1, aces
        assert "(I)" not in aces[0]  # nothing inherited from the parent folder
        assert aces[0].endswith("F)")


def _settings(home: Path) -> config.Settings:
    return config.Settings(api_id=1, api_hash=None, hidden_chats=(), expected_user=None, home=home)


def test_legacy_files_lists_clear_text_leftovers(tmp_path):
    settings = _settings(tmp_path)
    assert config.legacy_files(settings) == []
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_id": 1, "hidden_chats": []}', encoding="utf-8")
    assert config.legacy_files(settings) == []
    cfg.write_text('{"api_id": 1, "api_hash": "a"}', encoding="utf-8")
    assert config.legacy_api_hash(tmp_path) == "a"
    (tmp_path / "telegram.session").write_bytes(b"x")
    assert config.legacy_files(settings) == [tmp_path / "telegram.session", cfg]


def test_legacy_files_lists_a_stray_login_session(tmp_path):
    (tmp_path / "login.session").write_bytes(b"x")
    assert config.legacy_files(_settings(tmp_path)) == [tmp_path / "login.session"]


def test_remove_legacy_files_keeps_other_settings(tmp_path):
    settings = _settings(tmp_path)
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_id": 1, "api_hash": "a", "hidden_chats": ["@x"]}', encoding="utf-8")
    for name in config.LEGACY_SQLITE_FILES:
        (tmp_path / name).write_bytes(b"x")
    config.remove_legacy_files(settings)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"api_id": 1, "hidden_chats": ["@x"]}
    assert config.legacy_files(settings) == []
    assert config.legacy_api_hash(tmp_path) is None
