"""Builders for model instances used across the tests."""

from datetime import UTC, datetime
from typing import Any

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from tgmcp.models import Chat, Message, Topic

_WHEN = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def make_message(id: int, **overrides: Any) -> Message:
    fields: dict[str, Any] = {
        "id": id,
        "chat_id": -1001,
        "topic_id": None,
        "date": _WHEN,
        "sender_id": 222,
        "sender_name": "Sam",
        "text": "hello",
    }
    return Message(**{**fields, **overrides})


def make_chat(id: int, **overrides: Any) -> Chat:
    fields: dict[str, Any] = {"id": id, "title": "Chat", "kind": "group"}
    return Chat(**{**fields, **overrides})


def make_topic(id: int, **overrides: Any) -> Topic:
    fields: dict[str, Any] = {"id": id, "title": "Topic"}
    return Topic(**{**fields, **overrides})


def make_session_string(key_byte: int = 3) -> str:
    """A Telethon StringSession with a dummy login key, as `secrets.bin` stores it."""
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes([key_byte]) * 256)
    text: str = session.save()
    return text
