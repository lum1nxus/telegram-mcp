import dataclasses
import logging
import os
import time
from pathlib import Path
from typing import ClassVar

import pytest
from telethon.sessions import SQLiteSession, StringSession

from tests.factories import make_session_string
from tgmcp import config, gateway, logging_setup, secrets
from tgmcp.config import Settings
from tgmcp.gateway import NotLoggedIn, load_session_string


def test_missing_session_is_not_logged_in(tmp_path: Path):
    path = tmp_path / "telegram.session"
    with pytest.raises(NotLoggedIn, match="tgmcp login"):
        load_session_string(path)
    assert not path.exists()


def test_empty_sqlite_session_is_not_logged_in(tmp_path: Path):
    path = tmp_path / "telegram.session"
    session = SQLiteSession(str(path))
    session.save()
    session.close()
    assert path.exists()
    with pytest.raises(NotLoggedIn, match="tgmcp login"):
        load_session_string(path)


def test_logging_never_writes_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    log_path = tmp_path / "logs" / "tgmcp.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        written = logging_setup.configure(log_path)
        logging.getLogger("tgmcp.test").info("dialogs n=3")
        logging.getLogger("telethon.network").info("chatty")
        for handler in root.handlers:
            handler.flush()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "dialogs n=3" in captured.err
        assert written == log_path.parent / f"tgmcp-{os.getpid()}.log"
        text = written.read_text(encoding="utf-8")
        assert "INFO tgmcp.test dialogs n=3" in text
        assert "chatty" not in text
    finally:
        for handler in root.handlers:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_corrupt_session_is_not_logged_in(tmp_path: Path):
    path = tmp_path / "telegram.session"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(NotLoggedIn, match="unreadable"):
        load_session_string(path)


def test_configure_twice_does_not_duplicate(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logging_setup.configure(tmp_path / "a.log")
        logging_setup.configure(tmp_path / "a.log")
        logging.getLogger("tgmcp.test").info("once only")
        assert capsys.readouterr().err.count("once only") == 1
    finally:
        for handler in root.handlers:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_configure_removes_week_old_logs_of_other_processes(tmp_path: Path):
    old = tmp_path / "tgmcp-111.log"
    recent = tmp_path / "tgmcp-222.log"
    other = tmp_path / "notes.log"
    for path in (old, recent, other):
        path.write_text("x", encoding="utf-8")
    eight_days_ago = time.time() - 8 * 86400
    for path in (old, other):
        os.utime(path, (eight_days_ago, eight_days_ago))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        written = logging_setup.configure(tmp_path / "tgmcp.log")
    finally:
        for handler in root.handlers:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
    assert not old.exists()
    assert recent.exists() and other.exists()
    assert written.exists()


class Recorder:
    """Stands in for TelegramClient: records what the gateway built it from."""

    built: ClassVar[list[tuple[str, int, str]]] = []
    log_outs = 0

    def __init__(self, session: StringSession, api_id: int, api_hash: str, **_: object) -> None:
        Recorder.built.append((session.save(), api_id, api_hash))

    async def connect(self) -> None:
        return None

    async def log_out(self) -> None:
        Recorder.log_outs += 1

    async def disconnect(self) -> None:
        return None


@pytest.fixture
def stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(config, "restrict_to_owner", lambda path: None)
    monkeypatch.setattr(gateway, "TelegramClient", Recorder)
    Recorder.built = []
    Recorder.log_outs = 0
    return Settings(api_id=7, api_hash=None, hidden_chats=(), expected_user=None, home=tmp_path)


def test_factory_without_secrets_is_not_logged_in(stored: Settings):
    with pytest.raises(NotLoggedIn) as caught:
        gateway.factory(stored)
    assert str(caught.value) == "Not logged in: run `tgmcp login` in a terminal."


def test_factory_with_only_old_files_asks_to_move_them(stored: Settings):
    stored.legacy_session_path.write_bytes(b"old")
    with pytest.raises(NotLoggedIn) as caught:
        gateway.factory(stored)
    assert str(caught.value) == gateway.MIGRATE


def test_factory_with_undecryptable_secrets(stored: Settings, monkeypatch):
    stored.secrets_path.write_bytes(b"blob")

    def refuse(blob: bytes) -> bytes:
        raise secrets.SecretsError(secrets.FOREIGN)

    monkeypatch.setattr(secrets, "unprotect", refuse)
    with pytest.raises(NotLoggedIn, match="cannot be decrypted"):
        gateway.factory(stored)


def test_factory_takes_the_hash_from_the_secrets_unless_overridden(stored: Settings):
    text = make_session_string()
    secrets.write_secrets(stored.secrets_path, "stored-hash", text)
    gateway.factory(stored)
    gateway.factory(dataclasses.replace(stored, api_hash="env-hash"))
    assert Recorder.built == [(text, 7, "stored-hash"), (text, 7, "env-hash")]


async def test_end_session_uses_the_stored_login(stored: Settings):
    text = make_session_string()
    secrets.write_secrets(stored.secrets_path, "stored-hash", text)
    await gateway.end_session(stored)
    assert Recorder.built == [(text, 7, "stored-hash")]
    assert Recorder.log_outs == 1
