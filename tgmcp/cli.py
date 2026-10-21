"""The `tgmcp` command: login, whoami, serve, logout."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from tgmcp import logging_setup
from tgmcp.config import (
    ConfigError,
    Settings,
    home,
    load_settings,
    restrict_to_owner,
    save_credentials,
)
from tgmcp.gateway import (
    GatewayError,
    NotLoggedIn,
    end_session,
    factory,
    interactive_login,
)
from tgmcp.resolve import Directory
from tgmcp.server import build_server

ATTEMPTS = 3


def _say(text: str) -> None:
    print(text, file=sys.stdout)


def _complain(text: str) -> None:
    print(text, file=sys.stderr)


def _ask_credentials() -> Settings | None:
    """Prompt for the API id and hash, save them, and load the settings again."""
    for _ in range(ATTEMPTS):
        raw = input("API id: ").strip()
        if raw.isdecimal():
            api_hash = getpass.getpass("API hash: ").strip()
            if not api_hash:
                _complain("The API hash cannot be empty.")
                return None
            save_credentials(int(raw), api_hash, home())
            return load_settings()
        _complain("The API id must be a number.")
    _complain("Too many wrong answers; run `tgmcp login` again.")
    return None


def _login() -> int:
    try:
        settings = load_settings()
    except ConfigError:
        asked = _ask_credentials()
        if asked is None:
            return 1
        settings = asked
    settings.home.mkdir(parents=True, exist_ok=True)
    account = asyncio.run(interactive_login(settings))
    if settings.session_path.exists():
        restrict_to_owner(settings.session_path)
    _say(f"logged in as {account.name}")
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
        found = [c for c in dialogs if Directory(gateway, [entry]).is_hidden(c)]
        if found:
            for chat in found:
                _say(f"  hidden: {chat.title} ({chat.id})")
        else:
            _say(f"  hidden: {entry} (not found)")
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
    if not settings.session_path.exists():
        _say("no session to log out")
        return 0
    asyncio.run(end_session(settings))
    settings.session_path.unlink(missing_ok=True)
    _say("logged out")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgmcp", description="Read-only Telegram MCP server for Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="log in to Telegram and save the session")
    commands.add_parser("whoami", help="show the account, chat counts and hidden chats")
    commands.add_parser("serve", help="run the MCP server on stdio (started by Claude Code)")
    commands.add_parser("logout", help="end the login on Telegram and delete the session file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands = {
        "login": _login,
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
