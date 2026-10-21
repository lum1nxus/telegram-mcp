"""The API hash and the login key, encrypted at rest; the only module that calls DPAPI.

On Windows the file is protected with DPAPI in user scope (`CryptProtectData`): only the same
Windows user on the same computer can decrypt it, and a copy is useless elsewhere. It does not
protect against programs running as that user; no OS store does. On other platforms the
payload is stored as it is (`PLAIN`) and only the `0o600` file mode protects it.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from tgmcp import config

PLAIN: bool = sys.platform != "win32"
VERSION = 1
FOREIGN = (
    "The secrets file cannot be decrypted on this computer or user account: "
    "run `tgmcp login` again."
)
UNREADABLE = "The secrets file is unreadable: run `tgmcp login` again."


class SecretsError(Exception):
    """The secrets file exists but cannot be used; the message is one sentence for the user."""


class NoSecrets(Exception):  # noqa: N818 - a state, not a failure
    """There is no secrets file: nobody has logged in yet (or `logout` removed it)."""


@dataclass(frozen=True)
class Secrets:
    api_hash: str
    session: str


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _UI_FORBIDDEN = 0x1  # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt (serve runs unattended)

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
        """`CryptProtectData` or `CryptUnprotectData`, user scope, no optional entropy (a
        constant in a public repository protects nothing). Raises OSError on failure."""
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        blob_out = _Blob()
        crypt32 = ctypes.windll.crypt32
        if encrypt:
            ok = crypt32.CryptProtectData(
                ctypes.byref(blob_in),
                "tgmcp",
                None,
                None,
                None,
                _UI_FORBIDDEN,
                ctypes.byref(blob_out),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(blob_in),
                None,
                None,
                None,
                None,
                _UI_FORBIDDEN,
                ctypes.byref(blob_out),
            )
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt for the current Windows user; the identity where `PLAIN`."""
    if PLAIN or sys.platform != "win32":
        return data
    return _dpapi(data, encrypt=True)


def unprotect(blob: bytes) -> bytes:
    """Decrypt what `protect` made; another user, another computer or a changed byte fail."""
    if PLAIN or sys.platform != "win32":
        return blob
    try:
        return _dpapi(blob, encrypt=False)
    except OSError as exc:
        raise SecretsError(FOREIGN) from exc


def write_secrets(path: Path, api_hash: str, session: str) -> None:
    """Replace the secrets file atomically, then restrict it to the owner."""
    payload = json.dumps({"v": VERSION, "api_hash": api_hash, "session": session})
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(protect(payload.encode("utf-8")))
    os.replace(temp, path)
    config.restrict_to_owner(path)


def read_secrets(path: Path) -> Secrets:
    try:
        blob = path.read_bytes()
    except FileNotFoundError as exc:
        raise NoSecrets() from exc
    data = unprotect(blob)
    try:
        found = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecretsError(UNREADABLE) from exc
    if not isinstance(found, dict) or found.get("v") != VERSION:
        raise SecretsError(UNREADABLE)
    api_hash, session = found.get("api_hash"), found.get("session")
    if not isinstance(api_hash, str) or not isinstance(session, str):
        raise SecretsError(UNREADABLE)
    return Secrets(api_hash=api_hash, session=session)
