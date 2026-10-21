"""The `tgmcp` command: login, whoami, serve, logout, ask and bench."""

import argparse
import asyncio
import getpass
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tgmcp import logging_setup, secrets
from tgmcp.config import (
    ConfigError,
    Settings,
    ensure_home,
    forget_credentials,
    home,
    legacy_api_hash,
    legacy_files,
    load_settings,
    remove_legacy_files,
    save_credentials,
)
from tgmcp.gateway import MIGRATE, NO_TERMINAL, ApiKeyError, GatewayError, NotLoggedIn
from tgmcp.host.backends import BackendError, ChatBackend, RemoteRefused, make_backend
from tgmcp.host.benchdata import BENCH_HIDDEN_CHATS, QUESTIONS, SMOKE_IDS, Question, build_seed
from tgmcp.host.loop import DEFAULT_MAX_STEPS, RunResult, Step, run_question
from tgmcp.host.scorer import Score, score, table
from tgmcp.resolve import Directory
from tgmcp.server import build_server
from tgmcp.telegram import end_session, factory, interactive_login

ATTEMPTS = 3
PLAIN_WARNING = "warning: secrets are not encrypted on this platform (file permissions only)"


def _say(text: str) -> None:
    print(text, file=sys.stdout)


def _complain(text: str) -> None:
    print(text, file=sys.stderr)


def _ask_api_id() -> Settings | None:
    """Prompt for the API id, save it, and load the settings again."""
    try:
        for _ in range(ATTEMPTS):
            raw = input("API id: ").strip()
            if raw.isdecimal():
                save_credentials(int(raw), home())
                return load_settings()
            _complain("The API id must be a number.")
    except EOFError:
        _complain(NO_TERMINAL)
        return None
    _complain("Too many wrong answers; run `tgmcp login` again.")
    return None


def _ask_api_hash() -> str | None:
    """Prompt for the API hash without echo; it is saved only inside the secrets file."""
    try:
        for _ in range(ATTEMPTS):
            api_hash = getpass.getpass("API hash: ").strip()
            if api_hash:
                return api_hash
            _complain("The API hash cannot be empty.")
    except EOFError:
        _complain(NO_TERMINAL)
        return None
    _complain("Too many wrong answers; run `tgmcp login` again.")
    return None


def _known_api_hash(settings: Settings) -> str | None:
    """TGMCP_API_HASH, else the one in the secrets file, else an old clear-text one."""
    if settings.api_hash:
        return settings.api_hash
    try:
        return secrets.read_secrets(settings.secrets_path).api_hash
    except (secrets.NoSecrets, secrets.SecretsError):
        return legacy_api_hash(settings.home)


def _login(reset: bool = False) -> int:
    if reset:  # forget the saved API id and hash (the secrets file) and ask for them again
        forget_credentials(home())
    try:
        settings = load_settings()
    except ConfigError:
        asked = _ask_api_id()
        if asked is None:
            return 1
        settings = asked
    api_hash = _known_api_hash(settings) or _ask_api_hash()
    if api_hash is None:
        return 1
    ensure_home(settings.home)
    try:
        account = asyncio.run(interactive_login(settings, api_hash, reuse=not reset))
    except GatewayError as exc:
        if isinstance(exc, ApiKeyError):  # wrong id or hash: keeping them would fail again
            forget_credentials(settings.home)
        _complain(str(exc))
        return 1
    _say(f"logged in as {account.name}")
    if legacy_files(settings):
        try:
            remove_legacy_files(settings)
        except OSError:  # an older tgmcp process still holds telegram.session open
            _complain(
                f"could not remove the old clear-text files in {settings.home} (in use): "
                "delete them after closing older tgmcp processes"
            )
        else:
            _say("removed the old clear-text files")
    return 0


async def _whoami(settings: Settings) -> int:
    gateway = factory(settings)
    try:
        me = await gateway.me()
        dialogs = await gateway.dialogs()
    finally:
        await gateway.close()
    directory = Directory(gateway, settings.hidden_chats)
    visible = [c for c in dialogs if not directory.is_hidden(c)]
    handle = f" (@{me.username})" if me.username else ""
    archived = sum(1 for c in visible if c.archived)
    hidden = len(dialogs) - len(visible)
    _say(f"account: {me.name}{handle}")
    _say(f"chats: {len(visible)} (archived {archived}, hidden {hidden})")
    for entry in settings.hidden_chats:
        found = [c for c in dialogs if entry.strip().lower() in Directory.hidden_keys(c)]
        if found:
            for chat in found:
                _say(f"  hidden: {chat.title} ({chat.id})")
        else:
            _say(f"  hidden: {entry} (not found)")
    if secrets.PLAIN:
        _say(PLAIN_WARNING)
    _say(f"home: {settings.home}")
    return 0


def _whoami_command() -> int:
    try:
        return asyncio.run(_whoami(load_settings()))
    except (NotLoggedIn, ConfigError) as exc:
        _complain(str(exc))
        return 2
    except GatewayError as exc:
        _complain(str(exc))
        return 1


def _serve() -> int:
    try:
        settings = load_settings()
        ensure_home(settings.home)
        logging_setup.configure(settings.log_template)
        gateway = factory(settings)
    except (NotLoggedIn, ConfigError) as exc:
        _complain(str(exc))
        return 2
    build_server(gateway, settings).run()  # Ctrl-C is handled in main
    return 0


def _logout() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        _complain(str(exc))
        return 2
    if not settings.secrets_path.exists():
        if legacy_files(settings):
            _complain(MIGRATE)
            return 2
        _say("no session to log out")
        return 0
    try:
        asyncio.run(end_session(settings))
        confirmed = True
    except Exception:  # revoked key, undecryptable file, no network: the file goes anyway
        confirmed = False
    settings.secrets_path.unlink(missing_ok=True)
    if confirmed:
        _say("logged out")
    else:
        _say(
            "logged out (Telegram did not confirm; also end the session under "
            "Settings → Devices in the Telegram app)"
        )
    return 0


def _args_text(arguments: dict[str, Any]) -> str:
    return ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items()
    )


def _call_line(step: Step, *, size: bool) -> str:
    line = f"-> {step.call.name}({_args_text(step.call.arguments)})"
    if size:
        line += f" {len(step.result_text)} chars"
        line += ", cut" if step.cut else ""
        line += ", error" if step.is_error else ""
    return line


def _first_cause(exc: BaseException) -> BaseException:
    """The first exception inside the task groups the MCP client wraps errors in."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def _ask(
    question: str, backend: ChatBackend, settings: Settings, max_steps: int
) -> RunResult:
    await backend.check_model()  # before Telegram: a missing model costs no connection
    server = build_server(factory(settings), settings)
    return await run_question(question, backend, server, max_steps=max_steps)


def _ask_command(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
        backend = make_backend(
            args.backend, args.model or settings.local_model, args.url, think=args.think
        )
        run = asyncio.run(_ask(args.question, backend, settings, args.max_steps))
    except Exception as raised:  # the MCP client wraps errors of the loop in task groups
        exc = _first_cause(raised)
        if not isinstance(exc, (ConfigError, RemoteRefused, BackendError, GatewayError)):
            raise
        _complain(str(exc))
        return 2 if isinstance(exc, (NotLoggedIn, ConfigError)) else 1
    if args.show_calls:
        for step in run.steps:
            _complain(_call_line(step, size=True))
    if run.finished:
        _say(run.answer or "")
        return 0
    _say(f"(unfinished after {len(run.steps)} steps)")
    if run.answer:
        _say(run.answer)
    else:
        for step in run.steps:
            _say(_call_line(step, size=False))
    return 1


@dataclass
class _BenchRun:
    question: Question
    number: int
    score: Score
    messages: list[dict[str, Any]]
    error: str | None = None  # the exception type when the run raised
    backend_failure: str | None = None  # the sentence of a `BackendError`

    def as_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.question.id,
            "category": self.question.category,
            "run": self.number,
            "score": asdict(self.score),
            "messages": self.messages,
        }
        if self.error is not None:
            row["error"] = self.error
        return row


def _bench_settings() -> Settings:
    """The bench's own settings: no login, the seed's hidden chat, never the user's config."""
    return Settings(
        api_id=0, api_hash=None, hidden_chats=BENCH_HIDDEN_CHATS, expected_user=None, home=home()
    )


async def _bench_one(
    question: Question, number: int, backend: ChatBackend, max_steps: int
) -> _BenchRun:
    seed = build_seed()
    titles = {chat.title for chat in seed.chats}
    ids = {chat.id for chat in seed.chats}
    server = build_server(seed, _bench_settings())  # the fake gateway: never Telethon
    started = time.perf_counter()
    try:
        run = await run_question(question.text, backend, server, max_steps=max_steps)
    except Exception as raised:  # one broken run is scored zero; the bench goes on
        exc = _first_cause(raised)
        seconds = time.perf_counter() - started
        zero = Score(False, False, False, False, steps=0, cuts=0, seconds=seconds)
        failure = str(exc) if isinstance(exc, BackendError) else None
        return _BenchRun(question, number, zero, [], type(exc).__name__, failure)
    return _BenchRun(question, number, score(question, run, titles, ids), run.messages)


async def _bench(
    backend: ChatBackend, questions: list[Question], repeat: int, max_steps: int
) -> list[_BenchRun]:
    await backend.check_model()
    return [
        await _bench_one(question, number, backend, max_steps)
        for question in questions
        for number in range(1, repeat + 1)
    ]


def _bench_command(args: argparse.Namespace) -> int:
    chosen = set(SMOKE_IDS) if args.set == "smoke" else None
    questions = [q for q in QUESTIONS if chosen is None or q.id in chosen]
    try:
        backend = make_backend(args.backend, args.model, args.url, think=args.think)
        runs = asyncio.run(_bench(backend, questions, args.repeat, DEFAULT_MAX_STEPS))
    except (RemoteRefused, BackendError) as exc:
        _complain(str(exc))
        return 1
    header = (
        f"model={backend.model} backend={args.backend} "
        f"think={'on' if args.think else 'off'} repeat={args.repeat}"
    )
    # Every run is a row: a percentage over N runs per question is the mean of its flags.
    _say(table([(run.question, run.score) for run in runs], header))
    if args.json is not None:
        Path(args.json).write_text(
            json.dumps([run.as_json() for run in runs], ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
    failures = list(dict.fromkeys(r.backend_failure for r in runs if r.backend_failure))
    for sentence in failures:
        _complain(sentence)
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgmcp", description="Read-only Telegram MCP server for Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="log in to Telegram and save the session")
    login.add_argument(
        "--reset", action="store_true", help="forget the saved API id and hash and ask again"
    )
    commands.add_parser("whoami", help="show the account, chat counts and hidden chats")
    commands.add_parser("serve", help="run the MCP server on stdio (started by Claude Code)")
    commands.add_parser("logout", help="end the login on Telegram and remove the secrets file")
    ask = commands.add_parser(
        "ask", help="answer one question with a local model (Ollama, LM Studio)"
    )
    ask.add_argument("question", help="the question, in quotes")
    _model_options(ask)
    ask.add_argument(
        "--max-steps", type=_positive, default=DEFAULT_MAX_STEPS, help="model calls at most (8)"
    )
    ask.add_argument(
        "--show-calls", action="store_true", help="print each tool call and its size on stderr"
    )
    bench = commands.add_parser(
        "bench", help="score a local model on the built-in questions (no Telegram data)"
    )
    _model_options(bench, model_required=True)
    bench.add_argument("--set", choices=("default", "smoke"), default="default")
    bench.add_argument("--repeat", type=_positive, default=1, help="runs per question (1)")
    bench.add_argument("--json", help="write every run (messages and scores) to this file")
    return parser


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _model_options(parser: argparse.ArgumentParser, *, model_required: bool = False) -> None:
    parser.add_argument("--backend", choices=("ollama", "lmstudio"), default="ollama")
    parser.add_argument(
        "--model", required=model_required, help="the model name as the backend lists it"
    )
    parser.add_argument("--url", help="the backend's address on this computer")
    parser.add_argument("--think", action="store_true", help="let the model think first")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands: dict[str, Callable[[], int]] = {
        "login": lambda: _login(reset=args.reset),
        "whoami": _whoami_command,
        "serve": _serve,
        "logout": _logout,
        "ask": lambda: _ask_command(args),
        "bench": lambda: _bench_command(args),
    }
    try:
        return commands[args.command]()
    except KeyboardInterrupt:
        return 0 if args.command == "serve" else 130


if __name__ == "__main__":
    raise SystemExit(main())
