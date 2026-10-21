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
    expected = env.get("TGMCP_EXPECTED_USER") or data.get("expected_user")
    return Settings(
        api_id=api_id_int,
        api_hash=str(api_hash),
        hidden_chats=_split_chats(hidden if hidden is not None else data.get("hidden_chats")),
        expected_user=str(expected) if expected else None,
        home=root,
    )


def restrict_to_owner(path: Path) -> None:
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
    else:
        path.chmod(0o700)


def save_credentials(api_id: int, api_hash: str, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    restrict_to_owner(home)
    path = home / "config.json"
    old = _read_file(path)
    data = {
        "api_id": api_id,
        "api_hash": api_hash,
        "hidden_chats": old.get("hidden_chats", []),
        "expected_user": old.get("expected_user"),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
