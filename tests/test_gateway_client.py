"""TelethonGateway against a stub client: no network."""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors
from telethon.tl import functions, types

from tests.test_gateway_mappers import WHEN, _channel, _dialog
from tgmcp.config import Settings
from tgmcp.gateway import Busy, GatewayError, TelethonGateway

FORUM_ID = -1000000001005


def _m(mid: int, *, topic: int | None = None, **kw: Any) -> types.Message:
    reply = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic) if topic else None
    return types.Message(
        id=mid, peer_id=types.PeerChannel(1005), date=WHEN, message=f"m{mid}", reply_to=reply, **kw
    )


def _service(mid: int) -> types.MessageService:
    return types.MessageService(
        id=mid,
        peer_id=types.PeerChannel(1005),
        date=WHEN,
        action=types.MessageActionTopicCreate(title="Cars", icon_color=0),
    )


class StubClient:
    def __init__(self) -> None:
        self.connected = False
        self.connects = 0
        self.disconnects = 0
        self.dialog_loads = 0
        self.iter_calls: list[dict[str, Any]] = []
        self.requests: list[Any] = []
        self.results: list[Any] = []
        self.messages: dict[str, list[Any]] = {"ids": [], "before": [], "after": [], "plain": []}
        self.me_error: Exception | None = None
        self.entity_lookups: list[str] = []
        self._self_id = 1
        self._mb_entity_cache = SimpleNamespace(get=lambda _id: None)
        entity = _channel(megagroup=True, forum=True, username="expats")
        self.dialogs = [_dialog(entity, types.PeerChannel(1005))]

    async def connect(self) -> None:
        self.connects += 1
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> Any:
        if self.me_error is not None:
            raise self.me_error
        return types.User(id=100, first_name="Slava", username="slava", access_hash=1)

    async def get_input_entity(self, value: str) -> Any:
        self.entity_lookups.append(value)
        return types.InputPeerUser(user_id=333, access_hash=1)

    async def iter_dialogs(self, **_kw: Any) -> AsyncIterator[Any]:
        self.dialog_loads += 1
        for d in self.dialogs:
            yield d

    async def iter_messages(self, entity: Any, **kw: Any) -> AsyncIterator[Any]:
        self.iter_calls.append({"entity": entity, **kw})
        if "ids" in kw:
            key = "ids"
        elif kw.get("reverse"):
            key = "after"
        elif "offset_id" in kw and "min_id" not in kw and kw.get("offset_date") is None:
            key = "before"
        else:
            key = "plain"
        for m in self.messages[key]:
            yield m

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        return self.results.pop(0)


@pytest.fixture
async def gw(tmp_path: Any) -> AsyncIterator[TelethonGateway]:
    settings = Settings(api_id=1, api_hash="a", hidden_chats=(), expected_user=None, home=tmp_path)
    gateway = TelethonGateway(settings, "")
    gateway._tg = StubClient()
    yield gateway
    await gateway.close()


def _stub(gw: TelethonGateway) -> StubClient:
    client: StubClient = gw._tg
    return client


async def test_peer_cache_refreshes_once_then_explains(gw: TelethonGateway):
    stub = _stub(gw)
    stub.results.append(
        SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=2002, participants_count=40))
    )
    chat = await gw.chat_details(FORUM_ID)
    assert (chat.members, chat.discussion_chat_id) == (40, -1000000002002)
    assert isinstance(stub.requests[0], functions.channels.GetFullChannelRequest)
    assert stub.dialog_loads == 1
    with pytest.raises(GatewayError, match="That chat is not in your dialog list"):
        await gw.chat_details(-1009)
    assert stub.dialog_loads == 2
    stub.results.append(
        SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=None, participants_count=0))
    )
    again = await gw.chat_details(FORUM_ID)
    assert (again.members, again.discussion_chat_id) == (None, None)
    assert stub.dialog_loads == 2  # a cached peer needs no refresh


async def test_around_order_and_topic_confinement(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["ids"] = [_m(42, topic=7)]
    stub.messages["before"] = [_m(41, topic=7), _service(40), _m(39, topic=7)]
    stub.messages["after"] = [_m(43, topic=7), _m(44, topic=7)]
    before, target, after = await gw.around(FORUM_ID, 42, before=3, after=2)
    assert [m.id for m in before] == [39, 41]
    assert target.id == 42
    assert target.topic_id == 7
    assert [m.id for m in after] == [43, 44]
    context_calls = [c for c in stub.iter_calls if "ids" not in c]
    assert [c["reply_to"] for c in context_calls] == [7, 7]
    assert context_calls[0]["offset_id"] == 42
    assert (context_calls[1]["min_id"], context_calls[1]["reverse"]) == (42, True)


async def test_around_missing_target(gw: TelethonGateway):
    with pytest.raises(GatewayError, match="No message 42"):
        await gw.around(FORUM_ID, 42, before=1, after=1)


async def test_input_user_parsing(gw: TelethonGateway):
    stub = _stub(gw)
    assert await gw._input_user(stub, None) is None
    assert await gw._input_user(stub, "333") == 333
    peer = await gw._input_user(stub, "@anna")
    assert isinstance(peer, types.InputPeerUser)
    assert stub.entity_lookups == ["@anna"]
    with pytest.raises(GatewayError, match="sender must be a user id or @username"):
        await gw._input_user(stub, "Anna K")


async def test_search_in_chat_builds_search_request(gw: TelethonGateway):
    stub = _stub(gw)
    stub.results.append(SimpleNamespace(messages=[_m(5, topic=7), _service(6)], users=[], chats=[]))
    hits = await gw.search_in_chat(
        FORUM_ID,
        "lizing",
        topic=7,
        sender="333",
        since=date(2026, 10, 1),
        until=date(2026, 10, 2),
        limit=20,
    )
    request = stub.requests[0]
    assert isinstance(request, functions.messages.SearchRequest)
    assert (request.q, request.top_msg_id, request.from_id, request.limit) == ("lizing", 7, 333, 20)
    assert request.min_date == datetime(2026, 10, 1, tzinfo=UTC)
    assert request.max_date == datetime(2026, 10, 3, tzinfo=UTC)
    assert [h.id for h in hits] == [5]
    assert hits[0].topic_id == 7


async def test_search_in_chat_naive_datetime_is_utc(gw: TelethonGateway):
    stub = _stub(gw)
    stub.results.append(SimpleNamespace(messages=[], users=[], chats=[]))
    await gw.search_in_chat(
        FORUM_ID,
        "x",
        topic=None,
        sender=None,
        since=datetime(2026, 10, 1, 23, 0),
        until=datetime(2026, 10, 2, 1, 0),
        limit=5,
    )
    request = stub.requests[0]
    assert request.min_date == datetime(2026, 10, 1, tzinfo=UTC)
    assert request.max_date == datetime(2026, 10, 3, tzinfo=UTC)


async def test_history_naive_bounds_are_utc(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["plain"] = [_m(3), _service(2), _m(1)]
    out = await gw.history(
        FORUM_ID,
        topic=None,
        before_id=None,
        since=datetime(2026, 10, 1),
        until=datetime(2026, 10, 30),
        limit=10,
    )
    assert [m.id for m in out] == [3, 1]
    call = stub.iter_calls[-1]
    assert call["offset_date"] == datetime(2026, 10, 30, tzinfo=UTC)


async def test_flood_wait_becomes_busy(gw: TelethonGateway):
    stub = _stub(gw)
    stub.me_error = errors.FloodWaitError(request=None, capture=42)
    with pytest.raises(Busy) as caught:
        await gw.me()
    assert caught.value.seconds == 42
    assert gw._in_flight == 0


async def test_idle_during_request_keeps_connection(gw: TelethonGateway):
    stub = _stub(gw)
    async with gw._session():
        await gw._on_idle()
        assert stub.disconnects == 0
        assert gw._guard is not None
    assert gw._in_flight == 0
    await gw._on_idle()
    assert stub.disconnects == 1
    assert gw._ready is False
    assert (gw._guard, gw._guard_task) == (None, None)
    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == []


async def test_reconnect_leaves_one_guard(gw: TelethonGateway):
    stub = _stub(gw)
    await gw.me()
    first = gw._guard_task
    assert first is not None
    stub.connected = False  # the connection dropped
    await gw.me()
    assert stub.connects == 2
    await asyncio.sleep(0)
    assert first.cancelled()
    assert gw._guard_task is not first
    running = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert running == [gw._guard_task]


async def test_guard_fires_and_disconnects(gw: TelethonGateway):
    stub = _stub(gw)
    gw._idle_s = 0.02
    await gw.me()
    task = gw._guard_task
    assert task is not None
    await asyncio.wait_for(task, 1)
    assert stub.disconnects == 1
    await gw.me()
    assert stub.connects == 2


async def test_close_never_raises(gw: TelethonGateway):
    stub = _stub(gw)
    await gw.me()

    async def broken() -> None:
        raise OSError("socket")

    stub.disconnect = broken  # type: ignore[method-assign]
    await gw.close()


async def test_service_message_target_is_missing(gw: TelethonGateway):
    _stub(gw).messages["ids"] = [_service(42)]
    with pytest.raises(GatewayError, match="No message 42 in that chat"):
        await gw.around(FORUM_ID, 42, before=1, after=1)


async def test_unresolvable_username_is_unknown_sender(gw: TelethonGateway):
    stub = _stub(gw)

    async def unknown(value: str) -> Any:
        raise ValueError(f"Cannot find any entity corresponding to {value}")

    stub.get_input_entity = unknown  # type: ignore[method-assign]
    with pytest.raises(GatewayError, match="Unknown sender: pass a user id or @username"):
        await gw._input_user(stub, "@ghost")


async def test_mapping_bug_is_internal_error_with_traceback(
    gw: TelethonGateway, caplog: pytest.LogCaptureFixture
):
    broken = types.Message(id=9, peer_id=types.PeerChannel(1005), date=None, message="x")
    _stub(gw).messages["plain"] = [broken]
    caplog.set_level(logging.ERROR, logger="tgmcp.gateway")
    with pytest.raises(GatewayError) as caught:
        await gw.history(FORUM_ID, topic=None, before_id=None, since=None, until=WHEN, limit=5)
    assert str(caught.value) == "Internal error in the Telegram gateway: ValidationError."
    record = next(r for r in caplog.records if r.message == "internal gateway error")
    assert record.exc_info is not None
