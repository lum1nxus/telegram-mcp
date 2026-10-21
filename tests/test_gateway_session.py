import logging
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
        logging_setup.configure(log_path)
        logging.getLogger("tgmcp.test").info("dialogs n=3")
        logging.getLogger("telethon.network").info("chatty")
        for handler in root.handlers:
            handler.flush()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "dialogs n=3" in captured.err
        assert log_path.exists()
        text = log_path.read_text(encoding="utf-8")
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
