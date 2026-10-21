"""Output models of every MCP tool, and the size caps that keep answers small."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Kind = Literal["user", "group", "channel"]
MediaKind = Literal["photo", "document", "voice", "video", "sticker", "other"]

TEXT_CAP = 2000
RESULT_BUDGET = 60_000
SEARCH_NOTE = (
    "Telegram's search matches the beginnings of words, not fragments: "
    "'лизинг' finds 'лизингом' but not 'автолизинг'. Rephrase before concluding there is nothing."
)


class Chat(BaseModel):
    id: int
    title: str
    kind: Kind
    username: str | None = None
    members: int | None = None
    last_message_at: datetime | None = None
    archived: bool = False
    is_forum: bool = False
    discussion_chat_id: int | None = None


class Topic(BaseModel):
    id: int
    title: str
    last_message_at: datetime | None = None


class Message(BaseModel):
    id: int
    chat_id: int
    topic_id: int | None
    date: datetime
    sender_id: int | None
    sender_name: str | None
    text: str
    text_truncated: bool = False
    reply_to_id: int | None = None
    has_media: bool = False
    media_kind: MediaKind | None = None
    forwarded_from: str | None = None
    link: str | None = None


class SearchResult(BaseModel):
    query: str
    scope: str
    since: date | None
    until: date | None
    hits: list[Message]
    truncated: bool
    note: str


class Page(BaseModel):
    chat: Chat
    messages: list[Message]
    truncated: bool
    next_before_id: int | None


class Context(BaseModel):
    chat: Chat
    target: Message
    before: list[Message]
    after: list[Message]


class Account(BaseModel):
    id: int
    name: str
    username: str | None


def cap_text(text: str, cap: int = TEXT_CAP) -> tuple[str, bool]:
    """Cut text to `cap` characters; the flag says whether anything was cut."""
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def fit_budget(
    items: Sequence[BaseModel], budget: int = RESULT_BUDGET
) -> tuple[list[BaseModel], bool]:
    """Keep the longest prefix whose JSON size stays under `budget` (at least one item)."""
    kept: list[BaseModel] = []
    total = 0
    for item in items:
        total += len(item.model_dump_json())
        if kept and total > budget:
            break
        kept.append(item)
    return kept, len(kept) < len(items) or total > budget
