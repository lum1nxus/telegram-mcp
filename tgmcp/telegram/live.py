"""The live gateway: read-only Telegram access over one lazily connected `ReadOnlyClient`."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any, Protocol

from telethon import functions, utils
from telethon.tl import types

from tgmcp.config import Settings, legacy_files
from tgmcp.gateway import (
    IDLE_SECONDS,
    MIGRATE,
    RAW_PER_MESSAGE,
    Gateway,
    GatewayError,
    IdleGuard,
    NotLoggedIn,
    aware,
    day_end,
    day_start,
    utc_day,
)
from tgmcp.models import Account, Chat, Message, Topic
from tgmcp.secrets import NoSecrets, SecretsError, read_secrets
from tgmcp.telegram.client import make_client
from tgmcp.telegram.errors import map_error
from tgmcp.telegram.mappers import (
    chat_from_dialog,
    is_plain,
    message_from_telethon,
    topic_from_forum_topic,
)

log = logging.getLogger(__name__)


class _Client(Protocol):
    """The part of Telethon's client the gateway uses; `ReadOnlyClient` checks every request."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def is_user_authorized(self) -> bool: ...

    async def get_me(self) -> Any: ...

    async def get_input_entity(self, peer: Any) -> Any: ...

    def iter_dialogs(self, **kwargs: Any) -> AsyncIterator[Any]: ...

    def iter_messages(self, entity: Any, **kwargs: Any) -> AsyncIterator[Any]: ...

    async def __call__(self, request: Any) -> Any: ...


async def _collect(
    stream: AsyncIterator[Any], limit: int, too_old: Callable[[Any], bool]
) -> list[Any]:
    """Up to `limit` plain messages from a newest-first stream. Stops at the first raw message
    for which `too_old` is true, or after `limit * RAW_PER_MESSAGE` raw messages so a long run
    of service messages cannot keep it fetching."""
    out: list[Any] = []
    seen = 0
    async for m in stream:
        seen += 1
        if m is not None and too_old(m):
            break
        if is_plain(m):
            out.append(m)
            if len(out) >= limit:
                break
        if seen >= limit * RAW_PER_MESSAGE:
            log.info("page stopped after %d raw messages", seen)
            break
    return out


def _guard_done(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("idle guard failed: %s", type(task.exception()).__name__)


class TelethonGateway(Gateway):
    """Read-only Telegram access over one lazily connected client.

    The client is name-mangled (`__client`): a tool holds the gateway, and nothing on it
    hands out the Telethon client.
    """

    def __init__(
        self,
        settings: Settings,
        session_string: str,
        *,
        api_hash: str | None = None,
        client: _Client | None = None,
    ) -> None:
        self._settings = settings
        key = api_hash or settings.api_hash
        if not key:
            raise NotLoggedIn()
        self.__client: _Client = (
            client if client is not None else make_client(settings, session_string, key)
        )
        self._lock = asyncio.Lock()
        self._ready = False
        self._idle_s: float = IDLE_SECONDS
        self._guard: IdleGuard | None = None
        self._guard_task: asyncio.Task[None] | None = None
        self._in_flight = 0
        self._peers: dict[int, Any] = {}
        self._chats: dict[int, Chat] = {}

    # Connection --------------------------------------------------------------------------

    async def _client(self) -> _Client:
        """Connect when needed; the caller owns one in-flight slot on success."""
        async with self._lock:
            if not self._ready or not self.__client.is_connected():
                await self._open()
            self._in_flight += 1
            return self.__client

    async def _open(self) -> None:
        tg = self.__client
        await tg.connect()
        if not await tg.is_user_authorized():
            await tg.disconnect()
            raise NotLoggedIn()
        expected = self._settings.expected_user
        if expected:
            me = await tg.get_me()
            want = expected.lstrip("@").lower()
            if want not in {(me.username or "").lower(), str(me.id)}:
                await tg.disconnect()
                raise NotLoggedIn("This session belongs to another account.")
        self._ready = True
        self._start_guard()
        log.info("connected")

    def _stop_guard(self) -> None:
        if self._guard is not None:
            self._guard.stop()
        task = self._guard_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._guard = None
        self._guard_task = None

    def _start_guard(self) -> None:
        self._stop_guard()
        self._guard = IdleGuard(self._idle_s, self._on_idle)
        self._guard_task = asyncio.create_task(self._guard.run())
        self._guard_task.add_done_callback(_guard_done)

    async def _on_idle(self) -> None:
        async with self._lock:
            if self._in_flight:  # a request is running: wait another idle period
                self._start_guard()
                return
            self._ready = False
            self._stop_guard()
            await self.__client.disconnect()
        log.info("disconnected after idle")

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[_Client]:
        """The connected client; every Telethon error leaves as a GatewayError."""
        entered = False
        try:
            client = await self._client()
            entered = True
            yield client
        except Exception as e:
            error = map_error(e)
            log.warning("telegram error %s -> %s", type(e).__name__, type(error).__name__)
            raise error from e
        finally:
            if entered:
                self._in_flight -= 1
                if self._guard is not None:
                    self._guard.touch()

    async def close(self) -> None:
        """Never raises."""
        self._stop_guard()
        self._ready = False
        try:
            if self.__client.is_connected():
                await self.__client.disconnect()
        except Exception as e:
            log.warning("disconnect failed: %s", type(e).__name__)

    # Chats ---------------------------------------------------------------------------------

    async def _load_dialogs(self, client: _Client) -> list[Chat]:
        chats: list[Chat] = []
        async for d in client.iter_dialogs(archived=None):
            chat = chat_from_dialog(d)
            self._peers[chat.id] = d.input_entity
            self._chats[chat.id] = chat
            chats.append(chat)
        return chats

    async def _peer(self, client: _Client, chat_id: int) -> Any:
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
        since, until = aware(since), aware(until)
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            stream = client.iter_messages(
                peer, limit=None, offset_id=before_id or 0, offset_date=until, reply_to=topic
            )
            found = await _collect(stream, limit, lambda m: since is not None and m.date < since)
            out = [self._map(m, chat_id) for m in found]
        log.info("history chat=%s n=%d", chat_id, len(out))
        return out

    async def _input_user(self, client: _Client, value: str | None) -> Any:
        if value is None:
            return None
        numeric = re.fullmatch(r"\d+", value) is not None
        if not numeric and re.fullmatch(r"@\w+", value) is None:
            raise GatewayError("sender must be a user id or @username")
        try:
            # Resolved now: an id this process has not seen would fail later, inside Telethon.
            return await client.get_input_entity(int(value) if numeric else value)
        except ValueError as exc:  # Telethon could not resolve the id or username
            raise GatewayError(
                "Unknown sender: pass a user id or @username from this chat."
            ) from exc

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
            # A raw SearchRequest, not iter_messages: iter_messages sends GetReplies when
            # `reply_to` is set and silently ignores `search`, and it has no `min_date`.
            result = await client(
                functions.messages.SearchRequest(
                    peer=peer,
                    q=query,
                    filter=types.InputMessagesFilterEmpty(),
                    min_date=day_start(since),
                    max_date=day_end(until),
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
            # A raw result carries its users and chats apart; attach them for sender names,
            # as Telethon's own iterator does (`_finish_init` is its private hook for that).
            entities = {utils.get_peer_id(e): e for e in [*result.users, *result.chats]}
            found = [m for m in result.messages if is_plain(m)]
            for m in found:
                m._finish_init(client, entities, peer)
            out = [self._map(m, chat_id) for m in found]
        log.info("search_in_chat chat=%s n=%d", chat_id, len(out))
        return out

    async def search_global(
        self, query: str, *, since: date | None, until: date | None, limit: int
    ) -> list[Message]:
        first_day = utc_day(since) if since else None
        async with self._session() as client:
            # Newest first, starting at the end of `until`; stop at the first hit before `since`.
            stream = client.iter_messages(
                None, search=query, limit=None, offset_date=day_end(until)
            )
            found = await _collect(
                stream,
                limit,
                lambda m: first_day is not None and m.date.astimezone(UTC).date() < first_day,
            )
            out = [self._map(m, m.chat_id) for m in found]
        log.info("search_global n=%d", len(out))
        return out

    async def around(
        self, chat_id: int, message_id: int, *, before: int, after: int
    ) -> tuple[list[Message], Message, list[Message]]:
        async with self._session() as client:
            peer = await self._peer(client, chat_id)
            found = [m async for m in client.iter_messages(peer, ids=[message_id]) if is_plain(m)]
            if not found:
                raise GatewayError(f"No message {message_id} in that chat.")
            target = self._map(found[0], chat_id)
            # In a forum the context stays inside the target's topic.
            topic = target.topic_id if self._chats[chat_id].is_forum else None
            older = [
                m
                async for m in client.iter_messages(
                    peer, limit=before, offset_id=message_id, reply_to=topic
                )
                if is_plain(m)
            ]
            newer = [
                m
                async for m in client.iter_messages(
                    peer, limit=after, min_id=message_id, reverse=True, reply_to=topic
                )
                if is_plain(m)
            ]
            earlier = [self._map(m, chat_id) for m in reversed(older)]
            later = [self._map(m, chat_id) for m in newer]
        log.info("around chat=%s before=%d after=%d", chat_id, len(earlier), len(later))
        return earlier, target, later


def factory(settings: Settings) -> Gateway:
    """The gateway `serve` hands to the server; a missing login fails before it starts."""
    try:
        stored = read_secrets(settings.secrets_path)
    except NoSecrets:
        if legacy_files(settings):
            raise NotLoggedIn(MIGRATE) from None
        raise NotLoggedIn() from None
    except SecretsError as exc:
        raise NotLoggedIn(str(exc)) from exc
    return TelethonGateway(settings, stored.session, api_hash=settings.api_hash or stored.api_hash)
