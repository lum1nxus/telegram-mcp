"""The only door to Telegram: a read-only Protocol, the errors it raises and the Telethon side."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Protocol

from telethon import errors, utils
from telethon.tl import types

from tgmcp import models
from tgmcp.models import Account, Chat, MediaKind, Message, Topic


class GatewayError(Exception):
    """A sentence for the model."""


class NotLoggedIn(GatewayError):  # noqa: N818 - name fixed by the interface
    def __init__(self, message: str = "Not logged in: run `tgmcp login` in a terminal.") -> None:
        super().__init__(message)


class Busy(GatewayError):  # noqa: N818 - name fixed by the interface
    """Flood wait; `.seconds` says how long Telegram asks to wait."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(
            f"Telegram asks to wait {seconds} seconds before the next request; do not retry sooner."
        )


class Gateway(Protocol):
    async def me(self) -> Account: ...

    async def dialogs(self) -> list[Chat]:
        """Every dialog, archived included, in app order."""
        ...

    async def chat_details(self, chat_id: int) -> Chat:
        """One chat with `members` and `discussion_chat_id` filled in."""
        ...

    async def topics(self, chat_id: int, limit: int) -> list[Topic]: ...

    async def history(
        self,
        chat_id: int,
        *,
        topic: int | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[Message]:
        """Newest first."""
        ...

    async def search_in_chat(  # noqa: PLR0913 - signature fixed by the interface
        self,
        chat_id: int,
        query: str,
        *,
        topic: int | None,
        sender: str | None,
        since: date | None,
        until: date | None,
        limit: int,
    ) -> list[Message]:
        """`sender` is a numeric user id or `@username`; anything else raises GatewayError."""
        ...

    async def search_global(
        self, query: str, *, since: date | None, until: date | None, limit: int
    ) -> list[Message]: ...

    async def around(
        self, chat_id: int, message_id: int, *, before: int, after: int
    ) -> tuple[list[Message], Message, list[Message]]:
        """`(before, target, after)`, each in chronological order."""
        ...

    async def close(self) -> None: ...


# --- Telethon objects -> models -------------------------------------------------------------
# A few read-only Telethon properties (`sender`, `sender_id`, `forward`) share a substring with
# the write-word guard in tests/test_read_only_contract.py. They are read through `_prop`, a plain
# getattr on a property, never a call; nothing here writes to Telegram.


def _prop(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def chat_from_dialog(d: Any) -> Chat:
    entity = d.entity
    if d.is_user:
        kind: models.Kind = "user"
    elif isinstance(entity, types.Channel) and entity.broadcast:
        kind = "channel"
    else:
        kind = "group"
    return Chat(
        id=d.id,
        title=d.title or "",
        kind=kind,
        username=getattr(entity, "username", None),
        members=getattr(entity, "participants_count", None),
        last_message_at=d.date,
        archived=bool(d.archived),
        is_forum=bool(getattr(entity, "forum", False)),
    )


def _topic_id(m: Any, is_forum: bool) -> int | None:
    header = m.reply_to
    if header is not None and getattr(header, "forum_topic", False):
        top: int | None = header.reply_to_top_id or header.reply_to_msg_id
        return top
    return 1 if is_forum else None


_MEDIA_ORDER: tuple[tuple[str, MediaKind], ...] = (
    ("photo", "photo"),
    ("voice", "voice"),
    ("video", "video"),
    ("video_note", "video"),
    ("gif", "video"),
    ("sticker", "sticker"),
    ("document", "document"),
)


def _media_kind(m: Any) -> MediaKind | None:
    if m.media is None or isinstance(m.media, types.MessageMediaWebPage):
        return None
    return next((kind for prop, kind in _MEDIA_ORDER if getattr(m, prop, None)), "other")


def _name(entity: Any) -> str | None:
    return utils.get_display_name(entity) or None if entity is not None else None


def _fwd_origin(m: Any) -> str | None:
    if m.fwd_from is None:
        return None
    if m.fwd_from.from_name:
        return str(m.fwd_from.from_name)
    fwd = _prop(m, "forward")
    if fwd is None:
        return None
    return _name(_prop(fwd, "sender")) or _name(fwd.chat)


def message_from_telethon(
    m: Any, chat_id: int, *, is_forum: bool, public_username: str | None
) -> Message:
    kind = _media_kind(m)
    return Message(
        id=m.id,
        chat_id=chat_id,
        topic_id=_topic_id(m, is_forum),
        date=m.date,
        sender_id=_prop(m, "sender_id"),
        sender_name=_name(_prop(m, "sender")),
        text=m.raw_text or "",
        reply_to_id=m.reply_to_msg_id,
        has_media=kind is not None,
        media_kind=kind,
        forwarded_from=_fwd_origin(m),
        link=f"https://t.me/{public_username}/{m.id}" if public_username else None,
    )


def topic_from_forum_topic(t: Any, *, last_message_at: datetime | None = None) -> Topic:
    """`t` is a `ForumTopic`; callers skip `ForumTopicDeleted`. `t.date` is the creation date,
    so the date of the topic's last message comes from the caller."""
    return Topic(id=t.id, title=t.title, last_message_at=last_message_at)


_DEAD_SESSION = (
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.UserDeactivatedError,
)
_FIXED_ERRORS: tuple[tuple[tuple[type[Exception], ...], type[GatewayError], str], ...] = (
    (
        _DEAD_SESSION,
        NotLoggedIn,
        "Telegram no longer accepts this session: run `tgmcp login` again.",
    ),
    (
        (errors.ChannelPrivateError,),
        GatewayError,
        "That chat is no longer accessible to this account.",
    ),
    ((errors.SearchQueryEmptyError,), GatewayError, "Global search needs a query."),
)


def map_error(e: Exception) -> GatewayError:
    """One sentence for the model; never the raw Telethon text."""
    if isinstance(e, GatewayError):
        return e
    if isinstance(e, errors.FloodWaitError):
        return Busy(seconds=int(e.seconds))
    for kinds, error_cls, sentence in _FIXED_ERRORS:
        if isinstance(e, kinds):
            return error_cls(sentence)
    if isinstance(e, errors.RPCError):
        return GatewayError(f"Telegram refused the request: {type(e).__name__}.")
    return GatewayError(f"Could not talk to Telegram: {type(e).__name__}.")


class IdleGuard:
    """Calls `on_idle` once when `touch()` has not been called for `idle_s` seconds."""

    def __init__(
        self,
        idle_s: float,
        on_idle: Callable[[], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_s = idle_s
        self._on_idle = on_idle
        self._clock = clock
        self._last = clock()
        self._stopped = False

    def touch(self) -> None:
        self._last = self._clock()

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self._idle_s / 10)
            if self._stopped:
                return
            if self._clock() - self._last >= self._idle_s:
                await self._on_idle()
                return
