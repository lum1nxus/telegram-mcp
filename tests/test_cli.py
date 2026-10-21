import json
from pathlib import Path

import pytest

from tests.factories import make_chat, make_session_string
from tests.fake_gateway import FakeGateway
from tgmcp import cli, config, secrets
from tgmcp.config import Settings
from tgmcp.gateway import ApiKeyError, GatewayError
from tgmcp.models import Account

MIGRATE = (
    "Old clear-text files found: run `tgmcp login` once to move them into the encrypted store."
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TGMCP_HOME", str(tmp_path))
    monkeypatch.setenv("TGMCP_API_ID", "1")
    monkeypatch.setenv("TGMCP_API_HASH", "a")
    monkeypatch.delenv("TGMCP_HIDDEN_CHATS", raising=False)
    monkeypatch.delenv("TGMCP_EXPECTED_USER", raising=False)
    monkeypatch.setattr(config, "restrict_to_owner", lambda path: None)
    return tmp_path


async def _logged_in(settings: Settings, api_hash: str) -> Account:
    """What a successful interactive_login leaves behind: the encrypted secrets file only."""
    secrets.write_secrets(settings.secrets_path, api_hash, make_session_string())
    return Account(id=9, name="Slava", username=None)


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


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


def test_serve_with_only_old_files_asks_to_move_them(env, capsys, monkeypatch):
    monkeypatch.setattr(cli.logging_setup, "configure", lambda path: path)
    (env / "telegram.session").write_bytes(b"old login")
    assert cli.main(["serve"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == MIGRATE


def test_help_lists_four_commands(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for command in ("login", "whoami", "serve", "logout"):
        assert command in out


def test_whoami_prints_account_counts_and_hidden(env, capsys, monkeypatch):
    monkeypatch.setenv("TGMCP_HIDDEN_CHATS", "Secret,Ghost")
    monkeypatch.setattr(secrets, "PLAIN", False)
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
    assert not [line for line in lines if line.startswith("warning:")]
    assert "close" in fake.calls


def test_whoami_warns_when_secrets_are_not_encrypted(env, capsys, monkeypatch):
    monkeypatch.setattr(secrets, "PLAIN", True)
    monkeypatch.setattr(cli, "factory", lambda settings: FakeGateway([], {}))
    assert cli.main(["whoami"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "warning: secrets are not encrypted on this platform (file permissions only)" in lines


def test_whoami_without_session_exits_2(env, capsys):
    assert cli.main(["whoami"]) == 2
    assert "tgmcp login" in capsys.readouterr().err


def test_whoami_with_only_old_files_asks_to_move_them(env, capsys):
    (env / "config.json").write_text('{"api_id": 1, "api_hash": "old"}', encoding="utf-8")
    assert cli.main(["whoami"]) == 2
    assert capsys.readouterr().err.strip() == MIGRATE


def test_login_with_saved_credentials_leaves_only_the_secrets_file(env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "interactive_login", _logged_in)
    assert cli.main(["login"]) == 0
    assert "logged in as Slava" in capsys.readouterr().out
    assert _names(env) == ["secrets.bin"]
    assert secrets.read_secrets(env / "secrets.bin").api_hash == "a"


def test_login_prompts_for_missing_credentials(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    monkeypatch.setattr("builtins.input", lambda prompt="": "42")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "hash")

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        assert (settings.api_id, api_hash) == (42, "hash")
        return await _logged_in(settings, api_hash)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    assert cli.main(["login"]) == 0
    saved = json.loads((env / "config.json").read_text(encoding="utf-8"))
    assert saved["api_id"] == 42
    assert "api_hash" not in saved


def test_login_takes_the_hash_from_the_secrets_file(env, monkeypatch):
    monkeypatch.delenv("TGMCP_API_HASH")
    secrets.write_secrets(env / "secrets.bin", "stored", make_session_string())

    def no_prompt(prompt: str = "") -> str:
        raise AssertionError(prompt)

    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    seen: list[str] = []

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        seen.append(api_hash)
        return await _logged_in(settings, api_hash)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    assert cli.main(["login"]) == 0
    assert seen == ["stored"]


def test_login_moves_old_clear_text_files(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    (env / "config.json").write_text(
        '{"api_id": 1, "api_hash": "old", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    (env / "telegram.session").write_bytes(b"old login")
    seen: list[str] = []

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        seen.append(api_hash)
        return await _logged_in(settings, api_hash)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    assert cli.main(["login"]) == 0
    assert seen == ["old"]
    assert _names(env) == ["config.json", "secrets.bin"]
    saved = json.loads((env / "config.json").read_text(encoding="utf-8"))
    assert saved == {"api_id": 1, "hidden_chats": ["@x"]}
    assert "removed the old clear-text files" in capsys.readouterr().out


def test_failed_login_keeps_old_files(env, monkeypatch):
    (env / "telegram.session").write_bytes(b"old login")

    async def failing(settings: Settings, api_hash: str) -> Account:
        raise GatewayError("Could not reach Telegram: check the network and try again.")

    monkeypatch.setattr(cli, "interactive_login", failing)
    assert cli.main(["login"]) == 1
    assert (env / "telegram.session").exists()


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


def test_logout_with_only_old_files_asks_to_move_them(env, capsys):
    (env / "telegram.session").write_bytes(b"old login")
    assert cli.main(["logout"]) == 2
    assert capsys.readouterr().err.strip() == MIGRATE


def test_logout_removes_secrets_file(env, capsys, monkeypatch):
    calls: list[Settings] = []

    async def fake_end(settings: Settings) -> None:
        calls.append(settings)

    monkeypatch.setattr(cli, "end_session", fake_end)
    stored = env / "secrets.bin"
    stored.write_bytes(b"blob")
    assert cli.main(["logout"]) == 0
    assert calls and not stored.exists()
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


def test_logout_removes_secrets_even_when_telegram_fails(env, capsys, monkeypatch):
    async def failing_end(settings: Settings) -> None:
        raise OSError("down")

    monkeypatch.setattr(cli, "end_session", failing_end)
    stored = env / "secrets.bin"
    stored.write_bytes(b"blob")
    assert cli.main(["logout"]) == 0
    assert not stored.exists()
    assert "Telegram did not confirm" in capsys.readouterr().out


def test_login_failure_prints_sentence_and_exits_1(env, capsys, monkeypatch):
    async def failing(settings: Settings, api_hash: str) -> Account:
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
    asked: list[str] = []

    def empty(prompt: str = "") -> str:
        asked.append(prompt)
        return ""

    monkeypatch.setattr(cli.getpass, "getpass", empty)
    assert cli.main(["login"]) == 1
    assert len(asked) == 3
    assert "cannot be empty" in capsys.readouterr().err


def test_login_without_terminal_input_exits_1(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["login"]) == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_login_without_terminal_input_for_the_hash_exits_1(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_HASH")

    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(cli.getpass, "getpass", eof)
    assert cli.main(["login"]) == 1
    assert "interactive terminal" in capsys.readouterr().err


def _no_terminal_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    prompts: list[str] = []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr("builtins.input", answer)
    return prompts


def test_login_reset_asks_again_and_saves_new_credentials(env, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    (env / "config.json").write_text(
        '{"api_id": 1, "api_hash": "old", "hidden_chats": ["@x"]}', encoding="utf-8"
    )
    prompts = _no_terminal_input(monkeypatch, ["77"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "new")
    seen: list[tuple[int, str]] = []

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        seen.append((settings.api_id, api_hash))
        return await _logged_in(settings, api_hash)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    assert cli.main(["login", "--reset"]) == 0
    assert prompts == ["API id: "]
    assert seen == [(77, "new")]
    saved = json.loads((env / "config.json").read_text(encoding="utf-8"))
    assert saved == {"api_id": 77, "hidden_chats": ["@x"], "expected_user": None}
    assert secrets.read_secrets(env / "secrets.bin").api_hash == "new"


def test_login_reset_asks_for_the_hash_kept_in_the_secrets_file(env, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    (env / "config.json").write_text('{"api_id": 1}', encoding="utf-8")
    secrets.write_secrets(env / "secrets.bin", "stored", make_session_string())
    _no_terminal_input(monkeypatch, ["5"])
    hashes: list[str] = []

    def ask(prompt: str = "") -> str:
        hashes.append(prompt)
        return "fresh"

    monkeypatch.setattr(cli.getpass, "getpass", ask)
    seen: list[str] = []

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        seen.append(api_hash)
        return await _logged_in(settings, api_hash)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    assert cli.main(["login", "--reset"]) == 0
    assert (hashes, seen) == (["API hash: "], ["fresh"])


def test_login_forgets_rejected_credentials(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    monkeypatch.delenv("TGMCP_API_HASH")
    (env / "config.json").write_text(
        '{"api_id": 1, "api_hash": "bad", "hidden_chats": ["@x"]}', encoding="utf-8"
    )

    async def rejected(settings: Settings, api_hash: str) -> Account:
        raise ApiKeyError(
            "The API id or hash is wrong: run `tgmcp login --reset` to enter them again."
        )

    monkeypatch.setattr(cli, "interactive_login", rejected)
    assert cli.main(["login"]) == 1
    assert "tgmcp login --reset" in capsys.readouterr().err
    saved = json.loads((env / "config.json").read_text(encoding="utf-8"))
    assert saved == {"hidden_chats": ["@x"]}


def test_login_and_serve_restrict_a_new_home(tmp_path, monkeypatch):
    restricted: list[Path] = []
    monkeypatch.setattr(config, "restrict_to_owner", restricted.append)
    monkeypatch.setenv("TGMCP_API_ID", "1")
    monkeypatch.setenv("TGMCP_API_HASH", "a")

    async def fake_login(settings: Settings, api_hash: str) -> Account:
        return Account(id=9, name="Slava", username=None)

    monkeypatch.setattr(cli, "interactive_login", fake_login)
    login_home = tmp_path / "login"
    monkeypatch.setenv("TGMCP_HOME", str(login_home))
    assert cli.main(["login"]) == 0
    serve_home = tmp_path / "serve"
    monkeypatch.setenv("TGMCP_HOME", str(serve_home))
    monkeypatch.setattr(cli.logging_setup, "configure", lambda path: path)
    assert cli.main(["serve"]) == 2  # no secrets file
    assert restricted == [login_home, serve_home]
