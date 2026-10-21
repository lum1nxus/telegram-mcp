"""Output models of every MCP tool, and the size caps that keep answers small."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Kind = Literal["user", "group", "channel"]
MediaKind = Literal["photo", "document", "voice", "video", "sticker", "other"]
ScanMode = Literal["fragment", "words", "regex"]
StoppedBy = Literal["max_messages", "since", "start", "time", "flood", "limit"]

TEXT_CAP = 2000
# Bytes of compact UTF-8 JSON. mcp sends a result twice (structured content and a pretty-printed
# text copy of it), so a result at the budget is about 62 KB on the wire.
RESULT_BUDGET = 30_000
# A list result (list_chats, list_topics) gets one text block per item, so its copy weighs more.
LIST_BUDGET = 25_000
QUOTE_CAP = 60
SEARCH_NOTE = (
    "Telegram's search matches the beginnings of words, not fragments: "
    "'market' finds 'marketing' but not 'supermarket'. Rephrase before concluding there is nothing."
)
# Appended to SEARCH_NOTE when the cascade tried queries and none of them found anything.
NOTHING_FOR_QUERIES = "Nothing found for any of the {n} queries tried; see queries_tried."
NOTHING_FOR_ONE_QUERY = "Nothing found for the 1 query tried; see queries_tried."


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


class QueryTried(BaseModel):
    """One Telegram query of the search cascade and its hits (hidden chats not counted)."""

    query: str
    hits: int


class SearchResult(BaseModel):
    query: str  # as the caller wrote it; the queries actually sent are in `queries_tried`
    scope: str
    since: date | None
    until: date | None
    hits: list[Message]
    truncated: bool
    note: str
    queries_tried: list[QueryTried] = []  # in cascade order; empty when expand=False


class ScanResult(BaseModel):
    """What `scan_chat` covered and found; `note` restates the coverage in one sentence."""

    chat: Chat
    query: str
    mode: ScanMode
    tolerance: int
    scanned: int  # messages matched: the chat's plain messages, not only the sender's
    oldest_scanned: datetime | None
    newest_scanned: datetime | None
    reached_start: bool
    stopped_by: StoppedBy
    elapsed_s: float
    hits: list[Message]  # newest first
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
    truncated: bool = False


class Account(BaseModel):
    id: int
    name: str
    username: str | None


def cap_text(text: str, cap: int = TEXT_CAP) -> tuple[str, bool]:
    """Cut text to `cap` characters; the flag says whether anything was cut."""
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def json_size(item: BaseModel) -> int:
    """Bytes of the item's compact JSON in UTF-8 (Cyrillic takes two bytes a letter)."""
    return len(item.model_dump_json().encode("utf-8"))


def quoted(text: str, cap: int = QUOTE_CAP) -> str:
    """`repr(text)` cut to `cap` characters: how a chat title or a user's argument enters an
    error sentence, so it reads as a quotation, never as part of the sentence."""
    shown = repr(text)
    return shown if len(shown) <= cap else shown[: cap - 1] + "\u2026"


def fit_budget[M: BaseModel](
    items: Sequence[M], budget: int = RESULT_BUDGET
) -> tuple[list[M], bool]:
    """Keep the longest prefix whose JSON size in bytes stays under `budget` (at least one)."""
    kept: list[M] = []
    total = 0
    for item in items:
        total += json_size(item)
        if kept and total > budget:
            break
        kept.append(item)
    return kept, len(kept) < len(items) or total > budget
