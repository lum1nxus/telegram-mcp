import logging
import os
import time
from pathlib import Path

import pytest
from telethon.sessions import SQLiteSession

from tgmcp import logging_setup
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
