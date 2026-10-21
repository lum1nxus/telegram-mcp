"""Builders for model instances used across the tests."""

from datetime import UTC, datetime
from typing import Any

from tgmcp.models import Chat, Message, Topic

_WHEN = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def make_message(id: int, **overrides: Any) -> Message:
    fields: dict[str, Any] = {
        "id": id,
        "chat_id": -1001,
        "topic_id": None,
        "date": _WHEN,
        "sender_id": 222,
        "sender_name": "Marek",
        "text": "hello",
    }
    return Message(**{**fields, **overrides})


def make_chat(id: int, **overrides: Any) -> Chat:
    fields: dict[str, Any] = {"id": id, "title": "Chat", "kind": "group"}
    return Chat(**{**fields, **overrides})


def make_topic(id: int, **overrides: Any) -> Topic:
    fields: dict[str, Any] = {"id": id, "title": "Topic"}
    return Topic(**{**fields, **overrides})
