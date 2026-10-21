"""Settings, the data folder and owner-only permissions."""

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    hidden_chats: tuple[str, ...]
    expected_user: str | None
    home: Path

    @property
    def session_path(self) -> Path:
        return self.home / "telegram.session"

    @property
    def log_path(self) -> Path:
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
    return tuple(s for s in (str(i).strip() for i in items or []) if s)


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    root = home(env)
    data = _read_file(root / "config.json")
    api_id = env.get("TGMCP_API_ID") or data.get("api_id")
    api_hash = env.get("TGMCP_API_HASH") or data.get("api_hash")
    if not api_id or not api_hash:
        raise ConfigError("api_id and api_hash are missing; run 'tgmcp login' or set TGMCP_API_ID.")
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
        api_hash=str(api_hash),
        hidden_chats=_split_chats(hidden if hidden is not None else data.get("hidden_chats")),
        expected_user=str(expected) if expected else None,
        home=root,
    )


def _owner() -> str:
    """The Windows account name for icacls; `whoami` when USERNAME is empty."""
    user = os.environ.get("USERNAME", "").strip()
    if user:
        return user
    found = subprocess.run(["whoami"], capture_output=True, text=True, encoding="utf-8", check=True)
    return found.stdout.strip()


def restrict_to_owner(path: Path) -> None:
    """Only the current user may read or change `path` (a folder or a file)."""
    if sys.platform == "win32":
        # The inheritance flags (OI)(CI) are for folders; on a file they leave an empty ACL.
        rights = "(OI)(CI)F" if path.is_dir() else "F"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{_owner()}:{rights}"],
            encoding="utf-8",
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


def save_credentials(api_id: int, api_hash: str, home: Path) -> None:
    ensure_home(home)
    path = home / "config.json"
    old = _read_file(path)
    data = {
        "api_id": api_id,
        "api_hash": api_hash,
        "hidden_chats": old.get("hidden_chats", []),
        "expected_user": old.get("expected_user"),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def forget_credentials(home: Path) -> None:
    """Drop the API id and hash from config.json, keeping the other settings."""
    path = home / "config.json"
    if not path.exists():
        return
    data = _read_file(path)
    data.pop("api_id", None)
    data.pop("api_hash", None)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
