"""`tgmcp login` and `tgmcp logout`: the only code that signs in or out, through `AuthClient`."""

import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from telethon import errors, utils
from telethon.sessions import SQLiteSession, StringSession

from tgmcp.config import Settings
from tgmcp.gateway import ApiKeyError, Busy, GatewayError, NotLoggedIn
from tgmcp.models import Account
from tgmcp.secrets import NoSecrets, SecretsError, read_secrets, write_secrets
from tgmcp.telegram.client import AuthClient, ReadOnlyViolation
from tgmcp.telegram.errors import DEAD_SESSION, refused

_UNREADABLE = "The session file is unreadable: run `tgmcp login` again."


def load_session_string(path: Path) -> str:
    """A Telethon SQLite session file as a string (used to move an old clear-text one)."""
    if not path.exists():  # SQLiteSession would create an empty file
        raise NotLoggedIn()
    try:
        session = SQLiteSession(str(path))
        try:
            text: str = StringSession.save(session)
        finally:
            session.close()
    except sqlite3.Error as exc:
        raise NotLoggedIn(_UNREADABLE) from exc
    if not text or StringSession(text).auth_key is None:
        raise NotLoggedIn()
    return text


def _known_login(settings: Settings) -> str | None:
    """The login to reuse: the encrypted one, else an old clear-text session file."""
    try:
        return read_secrets(settings.secrets_path).session
    except (NoSecrets, SecretsError):
        pass
    try:
        return load_session_string(settings.legacy_session_path)
    except NotLoggedIn:
        return None


def _forget_dead_login(settings: Settings) -> None:
    settings.secrets_path.unlink(missing_ok=True)
    legacy = settings.legacy_session_path
    for path in (legacy, legacy.with_name(legacy.name + "-journal")):
        path.unlink(missing_ok=True)


def _auth_client(settings: Settings, session: str | None, api_hash: str) -> Any:
    return AuthClient(StringSession(session), settings.api_id, api_hash, receive_updates=False)


async def _console_login(  # noqa: C901 - one except per case
    settings: Settings, api_hash: str, seed: str | None
) -> tuple[Any, str]:
    """Telethon's console login on an in-memory session: the key never touches the disk in
    clear text. Returns the user and the login as a string."""
    client: Any = None
    dead = False
    try:
        client = _auth_client(settings, seed or None, api_hash)
        await client.start()
        me = await client.get_me()
        login: str = StringSession.save(client.session)
    except ReadOnlyViolation as e:  # before RuntimeError, which it is
        raise refused(e) from e
    except errors.ApiIdInvalidError as e:
        raise ApiKeyError(
            "The API id or hash is wrong: run `tgmcp login --reset` to enter them again."
        ) from e
    except DEAD_SESSION as e:  # an existing login that Telegram no longer accepts
        dead = True
        raise GatewayError(
            "Telegram no longer accepts the old session; it was removed. Run `tgmcp login` again."
        ) from e
    except RuntimeError as e:  # Telethon gives up after three wrong codes
        raise GatewayError("The code was wrong three times: run `tgmcp login` again.") from e
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as e:
        raise GatewayError("The code was wrong or expired: run `tgmcp login` again.") from e
    except (errors.SessionPasswordNeededError, errors.PasswordHashInvalidError) as e:
        raise GatewayError("The two-step password was wrong: run `tgmcp login` again.") from e
    except errors.PhoneNumberInvalidError as e:
        raise GatewayError("That phone number is not valid.") from e
    except errors.FloodWaitError as e:
        raise Busy(seconds=int(e.seconds)) from e
    except OSError as e:
        raise GatewayError("Could not reach Telegram: check the network and try again.") from e
    finally:
        if client is not None:
            await client.disconnect()
        if dead:
            _forget_dead_login(settings)
    return me, login


_NO_PERMISSIONS = (
    "The login was saved but its file permissions could not be set: "
    "run `tgmcp login` again or check icacls."
)


async def interactive_login(settings: Settings, api_hash: str, *, reuse: bool = True) -> Account:
    """Log in (Telethon asks on the console) and keep the login only in the encrypted secrets
    file. With `reuse`, an existing login (the secrets file, or an old clear-text session file)
    is reused, so Telegram does not ask for a code again; `login --reset` passes False."""
    me, login = await _console_login(settings, api_hash, _known_login(settings) if reuse else None)
    if not login:
        raise GatewayError("Telegram did not finish the login: run `tgmcp login` again.")
    try:
        write_secrets(settings.secrets_path, api_hash, login)
    except subprocess.CalledProcessError as e:  # icacls failed after the file was written
        raise GatewayError(_NO_PERMISSIONS) from e
    except OSError as e:
        raise GatewayError(f"Could not save the login in {settings.secrets_path}.") from e
    return Account(id=me.id, name=utils.get_display_name(me), username=me.username)


async def end_session(settings: Settings) -> None:
    """Ends the stored login on Telegram; the caller then removes the secrets file."""
    stored = read_secrets(settings.secrets_path)
    client = _auth_client(settings, stored.session, settings.api_hash or stored.api_hash)
    try:
        await client.connect()
        await client.log_out()
    finally:
        await client.disconnect()
