"""Builders for model instances used across the tests."""

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from tgmcp.host.benchdata import make_chat, make_message, make_topic

__all__ = ["make_chat", "make_message", "make_session_string", "make_topic"]


def make_session_string(key_byte: int = 3) -> str:
    """A Telethon StringSession with a dummy login key, as `secrets.bin` stores it."""
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes([key_byte]) * 256)
    text: str = session.save()
    return text
