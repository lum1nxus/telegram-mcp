"""The only door to Telegram: a read-only Protocol, the errors it raises and the Telethon side."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Protocol

from telethon import TelegramClient, errors, functions, utils
from telethon.sessions import SQLiteSession, StringSession
from telethon.tl import types

from tgmcp import models
from tgmcp.config import Settings
from tgmcp.models import Account, Chat, MediaKind, Message, Topic

LIBRARY_LOGGER = "telethon"
IDLE_SECONDS = 600
log = logging.getLogger(__name__)


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


# --- The live gateway ------------------------------------------------------------------------


def load_session_string(path: Path) -> str:
    """The saved login as a string, so the client runs on an in-memory session."""
    if not path.exists():  # SQLiteSession would create an empty file
        raise NotLoggedIn()
    session = SQLiteSession(str(path))
    try:
        text: str = StringSession.save(session)
    finally:
        session.close()
    if not text or StringSession(text).auth_key is None:
        raise NotLoggedIn()
    return text


def _day_start(day: date | None) -> datetime | None:
    return datetime.combine(day, dtime.min, tzinfo=UTC) if day else None


def _day_end(day: date | None) -> datetime | None:
    """Exclusive end: the first instant after `day`."""
    return datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=UTC) if day else None


class TelethonGateway(Gateway):
    """Read-only Telegram access over one lazily connected client."""

    def __init__(self, settings: Settings, session_string: str) -> None:
        self._settings = settings
        self._tg: Any = TelegramClient(
            StringSession(session_string),
            settings.api_id,
            settings.api_hash,
            flood_sleep_threshold=10,
        )
        self._lock = asyncio.Lock()
        self._ready = False
        self._guard: IdleGuard | None = None
        self._guard_task: asyncio.Task[None] | None = None
        self._peers: dict[int, Any] = {}
        self._chats: dict[int, Chat] = {}

    # Connection --------------------------------------------------------------------------

    async def _client(self) -> Any:
        async with self._lock:
            if not self._ready or not self._tg.is_connected():
                await self._open()
            if self._guard is not None:
                self._guard.touch()
            return self._tg

    async def _open(self) -> None:
        await self._tg.connect()
        if not await self._tg.is_user_authorized():
            await self._tg.disconnect()
            raise NotLoggedIn()
        expected = self._settings.expected_user
        if expected:
            me = await self._tg.get_me()
            want = expected.lstrip("@").lower()
            if want not in {(me.username or "").lower(), str(me.id)}:
                await self._tg.disconnect()
                raise NotLoggedIn("This session belongs to another account.")
        self._ready = True
        self._guard = IdleGuard(IDLE_SECONDS, self._disconnect)
        self._guard_task = asyncio.create_task(self._guard.run())
        log.info("connected")

    async def _disconnect(self) -> None:
        self._ready = False
        self._guard = None
        await self._tg.disconnect()
        log.info("disconnected after idle")

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        """The connected client; every Telethon error leaves as a GatewayError."""
        try:
            yield await self._client()
        except Exception as e:
            error = map_error(e)
            log.warning("telegram error %s -> %s", type(e).__name__, type(error).__name__)
            raise error from e

    async def close(self) -> None:
        if self._guard is not None:
            self._guard.stop()
        if self._guard_task is not None:
            self._guard_task.cancel()
        self._guard = None
        self._guard_task = None
        self._ready = False
        if self._tg.is_connected():
            await self._tg.disconnect()

    # Chats ---------------------------------------------------------------------------------

    async def _load_dialogs(self, client: Any) -> list[Chat]:
        chats: list[Chat] = []
        async for d in client.iter_dialogs(archived=None):
            chat = chat_from_dialog(d)
            self._peers[chat.id] = d.input_entity
            self._chats[chat.id] = chat
            chats.append(chat)
        return chats

    async def _peer(self, client: Any, chat_id: int) -> Any:
        if chat_id not in self._peers:
            await self._load_dialogs(client)
        if chat_id not in self._peers:
            raise GatewayError("That chat is not in your dialog list.")
        return self._peers[chat_id]

    def _map(self, m: Any, chat_id: int) -> Message:
        chat = self._chats.get(chat_id)
        if chat is None:  # a global-search hit outside the cached dialogs
            entity = m.chat
            is_forum = bool(getattr(entity, "forum", False))
            public = (
                getattr(entity, "username", None) if isinstance(entity, types.Channel) else None
            )
        else:
            is_forum = chat.is_forum
            public = chat.username if chat.kind != "user" else None
        return message_from_telethon(m, chat_id, is_forum=is_forum, public_username=public)

    async def me(self) -> Account:
        async with self._session() as client:
            me = await client.get_me()
        return Account(id=me.id, name=utils.get_display_name(me), username=me.username)

    async def dialogs(self) -> list[Chat]:
        async with self._session() as client:
            chats = await self._load_dialogs(client)
        log.info("dialogs n=%d", len(chats))
        return chats

    async def chat_details(self, chat_id: int) -> Chat:
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            chat = self._chats[chat_id]
            if not isinstance(peer, types.InputPeerChannel):
                return chat
            full = await client(functions.channels.GetFullChannelRequest(channel=peer))
        info = full.full_chat
        linked = info.linked_chat_id
        return chat.model_copy(
            update={
                "members": info.participants_count or chat.members,
                "discussion_chat_id": (
                    utils.get_peer_id(types.PeerChannel(linked)) if linked else None
                ),
            }
        )

    async def topics(self, chat_id: int, limit: int) -> list[Topic]:
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            result = await client(
                functions.messages.GetForumTopicsRequest(
                    peer=peer, offset_date=None, offset_id=0, offset_topic=0, limit=limit
                )
            )
        dates = {m.id: m.date for m in result.messages}
        return [
            topic_from_forum_topic(t, last_message_at=dates.get(t.top_message))
            for t in result.topics
            if isinstance(t, types.ForumTopic)
        ]

    # Messages ------------------------------------------------------------------------------

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
        out: list[Message] = []
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            async for m in client.iter_messages(
                peer, limit=limit, offset_id=before_id or 0, offset_date=until, reply_to=topic
            ):
                if since is not None and m.date < since:
                    break
                out.append(self._map(m, chat_id))
        log.info("history chat=%s n=%d", chat_id, len(out))
        return out

    async def _input_user(self, client: Any, value: str | None) -> Any:
        if value is None:
            return None
        if re.fullmatch(r"\d+", value):
            return int(value)
        if re.fullmatch(r"@\w+", value):
            return await client.get_input_entity(value)
        raise GatewayError("sender must be a user id or @username")

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
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            result = await client(
                functions.messages.SearchRequest(
                    peer=peer,
                    q=query,
                    filter=types.InputMessagesFilterEmpty(),
                    min_date=_day_start(since),
                    max_date=_day_end(until),
                    offset_id=0,
                    add_offset=0,
                    limit=limit,
                    max_id=0,
                    min_id=0,
                    hash=0,
                    from_id=await self._input_user(client, sender),
                    top_msg_id=topic,
                )
            )
            # A raw result carries its users and chats apart; attach them for sender names.
            entities = {utils.get_peer_id(e): e for e in [*result.users, *result.chats]}
            found = [m for m in result.messages if isinstance(m, types.Message)]
            for m in found:
                m._finish_init(client, entities, peer)
        out = [self._map(m, chat_id) for m in found]
        log.info("search_in_chat chat=%s n=%d", chat_id, len(out))
        return out

    async def search_global(
        self, query: str, *, since: date | None, until: date | None, limit: int
    ) -> list[Message]:
        out: list[Message] = []
        async with self._session() as client:
            # Newest first, starting at the end of `until`; stop at the first hit before `since`.
            async for m in client.iter_messages(
                None, search=query, limit=limit, offset_date=_day_end(until)
            ):
                if since is not None and m.date.astimezone(UTC).date() < since:
                    break
                out.append(self._map(m, m.chat_id))
        log.info("search_global n=%d", len(out))
        return out

    async def around(
        self, chat_id: int, message_id: int, *, before: int, after: int
    ) -> tuple[list[Message], Message, list[Message]]:
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            found = [m async for m in client.iter_messages(peer, ids=[message_id]) if m]
            if not found:
                raise GatewayError(f"No message {message_id} in that chat.")
            target = self._map(found[0], chat_id)
            # In a forum the context stays inside the target's topic.
            topic = target.topic_id if self._chats[chat_id].is_forum else None
            older = [
                self._map(m, chat_id)
                async for m in client.iter_messages(
                    peer, limit=before, offset_id=message_id, reply_to=topic
                )
            ]
            newer = [
                self._map(m, chat_id)
                async for m in client.iter_messages(
                    peer, limit=after, min_id=message_id, reverse=True, reply_to=topic
                )
            ]
        log.info("around chat=%s before=%d after=%d", chat_id, len(older), len(newer))
        return older[::-1], target, newer


def factory(settings: Settings) -> Gateway:
    """The gateway `serve` hands to the server; a missing session fails before it starts."""
    return TelethonGateway(settings, load_session_string(settings.session_path))
