from pathlib import Path

import pytest
from telethon.sessions import SQLiteSession

from tests.factories import make_chat
from tests.fake_gateway import FakeGateway
from tgmcp import cli
from tgmcp.config import Settings
from tgmcp.gateway import GatewayError
from tgmcp.models import Account


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TGMCP_HOME", str(tmp_path))
    monkeypatch.setenv("TGMCP_API_ID", "1")
    monkeypatch.setenv("TGMCP_API_HASH", "a")
    monkeypatch.delenv("TGMCP_HIDDEN_CHATS", raising=False)
    monkeypatch.delenv("TGMCP_EXPECTED_USER", raising=False)
    return tmp_path


def test_serve_without_session_exits_2_and_keeps_stdout_clean(env, capsys):
    assert cli.main(["serve"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "tgmcp login" in err


def test_serve_without_credentials_exits_2(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    assert cli.main(["serve"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "tgmcp login" in err


def test_help_lists_four_commands(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for command in ("login", "whoami", "serve", "logout"):
        assert command in out


def test_whoami_prints_account_counts_and_hidden(env, capsys, monkeypatch):
    monkeypatch.setenv("TGMCP_HIDDEN_CHATS", "Secret,Ghost")
    fake = FakeGateway(
        chats=[
            make_chat(id=1, title="Open"),
            make_chat(id=2, title="Secret"),
            make_chat(id=3, title="Old", archived=True),
        ],
        messages={},
    )
    monkeypatch.setattr(cli, "factory", lambda settings: fake)
    assert cli.main(["whoami"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "account: Alex (@alex)"
    assert lines[1] == "chats: 2 (archived 1, hidden 1)"
    assert "  hidden: Secret (2)" in lines
    assert "  hidden: Ghost (not found)" in lines
    assert lines[-1] == f"home: {env}"
    assert "close" in fake.calls


def test_whoami_without_session_exits_2(env, capsys):
    assert cli.main(["whoami"]) == 2
    assert "tgmcp login" in capsys.readouterr().err


def test_login_with_saved_credentials(env, capsys, monkeypatch):
    async def fake_login(settings: Settings) -> Account:
        settings.session_path.write_text("x", encoding="utf-8")
        return Account(id=9, name="Alex", username=None)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    monkeypatch.setattr(cli, "restrict_to_owner", lambda path: None)
    assert cli.main(["login"]) == 0
    assert "logged in as Alex" in capsys.readouterr().out


def test_login_prompts_for_missing_credentials(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    monkeypatch.setattr("builtins.input", lambda prompt="": "42")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "hash")

    async def fake_login(settings: Settings) -> Account:
        assert (settings.api_id, settings.api_hash) == (42, "hash")
        return Account(id=9, name="Alex", username="s")

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    monkeypatch.setattr(cli, "restrict_to_owner", lambda path: None)
    assert cli.main(["login"]) == 0
    assert (env / "config.json").exists()


def test_login_rejects_non_numeric_id_three_times(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    prompts: list[str] = []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return "abc"

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["login"]) == 1
    assert len(prompts) == 3
    assert "number" in capsys.readouterr().err


def test_logout_without_session(env, capsys):
    assert cli.main(["logout"]) == 0
    assert "no session to log out" in capsys.readouterr().out


def test_logout_removes_session_file(env, capsys, monkeypatch):
    calls: list[Settings] = []

    async def fake_end(settings: Settings) -> None:
        calls.append(settings)

    monkeypatch.setattr(cli, "end_session", fake_end)
    session = env / "telegram.session"
    session.write_text("x", encoding="utf-8")
    assert cli.main(["logout"]) == 0
    assert calls and not session.exists()
    assert "logged out" in capsys.readouterr().out


def test_serve_runs_server_and_returns_0(env, monkeypatch):
    ran: list[bool] = []

    class FakeServer:
        def run(self) -> None:
            ran.append(True)

    monkeypatch.setattr(cli, "factory", lambda settings: FakeGateway([], {}))
    monkeypatch.setattr(cli, "build_server", lambda make, settings: FakeServer())
    monkeypatch.setattr(cli.logging_setup, "configure", lambda path: None)
    assert cli.main(["serve"]) == 0
    assert ran == [True]


def test_logout_removes_session_even_when_telegram_fails(env, capsys, monkeypatch):
    async def failing_end(settings: Settings) -> None:
        raise OSError("down")

    monkeypatch.setattr(cli, "end_session", failing_end)
    session = env / "telegram.session"
    session.write_text("x", encoding="utf-8")
    assert cli.main(["logout"]) == 0
    assert not session.exists()
    assert "Telegram did not confirm" in capsys.readouterr().out


def test_aborted_login_removes_session_without_auth_key(env, monkeypatch):
    async def aborted(settings: Settings) -> Account:
        SQLiteSession(str(settings.session_path)).close()  # what Telethon leaves before the code
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "interactive_login", aborted)
    cli.main(["login"])
    assert not (env / "telegram.session").exists()


def test_login_failure_prints_sentence_and_exits_1(env, capsys, monkeypatch):
    async def failing(settings: Settings) -> Account:
        raise GatewayError("The code was wrong or expired: run `tgmcp login` again.")

    monkeypatch.setattr(cli, "interactive_login", failing)
    assert cli.main(["login"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "code was wrong" in err


def test_login_empty_hash_reprompts_then_gives_up(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    monkeypatch.setattr("builtins.input", lambda prompt="": "42")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "")
    assert cli.main(["login"]) == 1
    assert "cannot be empty" in capsys.readouterr().err


def test_login_without_terminal_input_exits_1(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["login"]) == 1
    assert "interactive terminal" in capsys.readouterr().err
