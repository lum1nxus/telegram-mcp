from pathlib import Path

import pytest

from tests.factories import make_chat
from tests.fake_gateway import FakeGateway
from tgmcp import cli
from tgmcp.config import Settings
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
    assert lines[0] == "account: Slava (@slava)"
    assert lines[1] == "chats: 2 (archived 1, hidden 1)"
    assert "  hidden: Secret (2)" in lines
    assert "  hidden: Ghost (not found)" in lines
    assert lines[-1] == f"home: {env}"


def test_whoami_without_session_exits_2(env, capsys):
    assert cli.main(["whoami"]) == 2
    assert "tgmcp login" in capsys.readouterr().err


def test_login_with_saved_credentials(env, capsys, monkeypatch):
    async def fake_login(settings: Settings) -> Account:
        settings.session_path.write_text("x", encoding="utf-8")
        return Account(id=9, name="Slava", username=None)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    monkeypatch.setattr(cli, "restrict_to_owner", lambda path: None)
    assert cli.main(["login"]) == 0
    assert "logged in as Slava" in capsys.readouterr().out


def test_login_prompts_for_missing_credentials(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    monkeypatch.setattr("builtins.input", lambda prompt="": "42")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "hash")

    async def fake_login(settings: Settings) -> Account:
        assert (settings.api_id, settings.api_hash) == (42, "hash")
        return Account(id=9, name="Slava", username="s")

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    monkeypatch.setattr(cli, "restrict_to_owner", lambda path: None)
    assert cli.main(["login"]) == 0
    assert (env / "config.json").exists()


def test_login_rejects_non_numeric_id_three_times(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    monkeypatch.setattr("builtins.input", lambda prompt="": "abc")
    assert cli.main(["login"]) == 1
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
