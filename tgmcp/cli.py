"""The `tgmcp` command: login, whoami, serve, logout."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

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
from tgmcp.gateway import (
    MIGRATE,
    ApiKeyError,
    GatewayError,
    NotLoggedIn,
    end_session,
    factory,
    interactive_login,
)
from tgmcp.resolve import Directory
from tgmcp.server import build_server

ATTEMPTS = 3
PLAIN_WARNING = "warning: secrets are not encrypted on this platform (file permissions only)"
NO_TERMINAL = "No terminal input: run `tgmcp login` in an interactive terminal."


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
        account = asyncio.run(interactive_login(settings, api_hash))
    except GatewayError as exc:
        if isinstance(exc, ApiKeyError):  # wrong id or hash: keeping them would fail again
            forget_credentials(settings.home)
        _complain(str(exc))
        return 1
    _say(f"logged in as {account.name}")
    if legacy_files(settings):
        remove_legacy_files(settings)
        _say("removed the old clear-text files")
    return 0


async def _whoami(settings: Settings) -> int:
    gateway = factory(settings)
    try:
        me = await gateway.me()
        dialogs = await gateway.dialogs()
        visible = await Directory(gateway, settings.hidden_chats).chats()
    finally:
        await gateway.close()
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
        logging_setup.configure(settings.log_path)
        gateway = factory(settings)
    except (NotLoggedIn, ConfigError) as exc:
        _complain(str(exc))
        return 2
    build_server(lambda _settings: gateway, settings).run()  # Ctrl-C is handled in main
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands = {
        "login": lambda: _login(reset=args.reset),
        "whoami": _whoami_command,
        "serve": _serve,
        "logout": _logout,
    }
    try:
        return commands[args.command]()
    except KeyboardInterrupt:
        return 0 if args.command == "serve" else 130


if __name__ == "__main__":
    raise SystemExit(main())
