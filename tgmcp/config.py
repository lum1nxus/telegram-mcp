"""Settings, the data folder and owner-only permissions."""

import functools
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The logger name of the Telegram library, kept here so logging setup needs no network code.
LIBRARY_LOGGER = "telethon"


class ConfigError(Exception):
    """The configuration is unusable; the message is one sentence for the user."""


def _default_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "telegram-mcp"
    return Path.home() / ".local" / "share" / "telegram-mcp"


DEFAULT_HOME: Path = _default_home()


def home(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = source.get("TGMCP_HOME")
    return Path(override) if override else _default_home()


SECRETS_FILE = "secrets.bin"
LEGACY_SESSION_FILE = "telegram.session"
# Clear-text SQLite files older versions may have left: the session, the temporary login
# session of an early build of the encrypted store, and their journals.
LEGACY_SQLITE_FILES = (
    LEGACY_SESSION_FILE,
    LEGACY_SESSION_FILE + "-journal",
    "login.session",
    "login.session-journal",
)


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str | None  # only TGMCP_API_HASH (development); normally inside secrets.bin
    hidden_chats: tuple[str, ...]
    expected_user: str | None
    home: Path

    @property
    def secrets_path(self) -> Path:
        """The API hash and the login key, encrypted (see `tgmcp.secrets`)."""
        return self.home / SECRETS_FILE

    @property
    def legacy_session_path(self) -> Path:
        """Where versions before the encrypted store kept the login key in clear text."""
        return self.home / LEGACY_SESSION_FILE

    @property
    def log_template(self) -> Path:
        """The name pattern of the log files: each `serve` writes `tgmcp-<pid>.log` (see
        `logging_setup.configure`); no file has this exact name."""
        return self.home / "tgmcp.log"


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    return data


def _split_chats(items: Any) -> tuple[str, ...]:
    if isinstance(items, str):
        items = items.split(",")
    if items is not None and not isinstance(items, list):
        raise ConfigError("hidden_chats must be a list of ids, @usernames or titles.")
    return tuple(s for s in (str(i).strip() for i in items or []) if s)


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    root = home(env)
    data = _read_file(root / "config.json")
    api_id = env.get("TGMCP_API_ID") or data.get("api_id")
    if not api_id:
        raise ConfigError("api_id is missing; run 'tgmcp login' or set TGMCP_API_ID.")
    try:
        api_id_int = int(api_id)
    except ValueError as exc:
        raise ConfigError("api_id must be a number.") from exc
    hidden = env.get("TGMCP_HIDDEN_CHATS")
    if hidden is not None and not hidden.strip():  # an empty variable counts as unset
        hidden = None
    expected = env.get("TGMCP_EXPECTED_USER") or data.get("expected_user")
    return Settings(
        api_id=api_id_int,
        api_hash=env.get("TGMCP_API_HASH") or None,
        hidden_chats=_split_chats(hidden if hidden is not None else data.get("hidden_chats")),
        expected_user=str(expected) if expected else None,
        home=root,
    )


def _system_tool(name: str) -> str:
    """A Windows tool by full path: Git Bash puts its own `whoami` first on PATH."""
    return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / name)


@functools.cache
def _owner_sid() -> str:
    """The current user's SID (`S-1-5-21-...`): icacls takes it as `*SID`, which works where an
    account name would not resolve (a renamed account, an empty or wrong USERNAME)."""
    found = subprocess.run(
        [_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    sid = found.stdout.strip().rsplit(",", 1)[-1].strip('"')
    if not sid.startswith("S-1-"):
        raise subprocess.CalledProcessError(0, found.args, found.stdout, found.stderr)
    return sid


def restrict_to_owner(path: Path) -> None:
    """Only the current user may read or change `path` (a folder or a file)."""
    if sys.platform == "win32":
        # The inheritance flags (OI)(CI) are for folders; on a file they leave an empty ACL.
        rights = "(OI)(CI)F" if path.is_dir() else "F"
        icacls = _system_tool("icacls.exe")
        # /reset drops every explicit entry (an administrator's temp folder carries SYSTEM,
        # Administrators and OWNER RIGHTS as explicit entries, which /grant:r would keep), so
        # that after removing inheritance exactly one entry remains: the owner's.
        for args in (
            ["/reset"],
            ["/inheritance:r", "/grant:r", f"*{_owner_sid()}:{rights}"],
        ):
            subprocess.run(
                [icacls, str(path), *args],
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=True,
            )
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def ensure_home(home: Path) -> Path:
    """Create the data folder restricted to the owner; an existing folder is left as it is."""
    if not home.exists():
        home.mkdir(parents=True)
        restrict_to_owner(home)
    return home


def _write_file(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_credentials(api_id: int, home: Path) -> None:
    """Save the API id; the API hash goes only into the encrypted secrets file."""
    ensure_home(home)
    path = home / "config.json"
    old = _read_file(path)
    data = {
        "api_id": api_id,
        "hidden_chats": old.get("hidden_chats", []),
        "expected_user": old.get("expected_user"),
    }
    _write_file(path, data)


def forget_credentials(home: Path) -> None:
    """Drop the API id (and an old clear-text hash) from config.json, keeping the other
    settings, and remove the secrets file, which holds the API hash."""
    (home / SECRETS_FILE).unlink(missing_ok=True)
    path = home / "config.json"
    if not path.exists():
        return
    data = _read_file(path)
    data.pop("api_id", None)
    data.pop("api_hash", None)
    _write_file(path, data)


def legacy_api_hash(home: Path) -> str | None:
    """The API hash an older version kept in clear text in config.json, if it is still there."""
    found = _read_file(home / "config.json").get("api_hash")
    return str(found) if found else None


def legacy_files(settings: Settings) -> list[Path]:
    """Clear-text secrets left by an older version: its session file and a config.json that
    still holds the API hash. `tgmcp login` moves them into the encrypted store."""
    found = [settings.home / n for n in LEGACY_SQLITE_FILES if (settings.home / n).exists()]
    if legacy_api_hash(settings.home) is not None:
        found.append(settings.home / "config.json")
    return found


def remove_legacy_files(settings: Settings) -> None:
    """After a successful login: drop the old session file and the clear-text API hash."""
    for name in LEGACY_SQLITE_FILES:
        (settings.home / name).unlink(missing_ok=True)
    path = settings.home / "config.json"
    data = _read_file(path)
    if "api_hash" in data:
        del data["api_hash"]
        _write_file(path, data)
