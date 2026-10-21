"""TelethonGateway against a stub client: no network."""

import asyncio
import logging
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors
from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession
from telethon.tl import functions, types

from tests.factories import make_session_string
from tests.test_gateway_mappers import WHEN, _channel, _dialog
from tgmcp import config, secrets
from tgmcp.config import Settings
from tgmcp.gateway import NO_TERMINAL, UNSEEN_USER, ApiKeyError, Busy, GatewayError
from tgmcp.telegram import auth
from tgmcp.telegram.client import ReadOnlyViolation
from tgmcp.telegram.live import TelethonGateway

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
        # A real in-memory session, as the gateway's client has: it knows Nora (333).
        self.session = StringSession()
        nora = types.User(id=333, first_name="Nora", username="Nora", access_hash=1)
        self.session.process_entities(types.contacts.ResolvedPeer(types.PeerUser(333), [], [nora]))
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
        return types.User(id=100, first_name="Alex", username="alex", access_hash=1)

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
    gateway = TelethonGateway(settings, "", client=StubClient())
    yield gateway
    await gateway.close()


def _stub(gw: TelethonGateway) -> StubClient:
    # The gateway keeps its client name-mangled so no tool can reach it; a test may.
    client: StubClient = gw._TelethonGateway__client  # type: ignore[attr-defined]
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


async def test_around_with_zero_window_fetches_only_the_target(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["ids"] = [_m(42, topic=7)]
    before, target, after = await gw.around(FORUM_ID, 42, before=0, after=0)
    assert (before, target.id, after) == ([], 42, [])
    assert [c for c in stub.iter_calls if "ids" not in c] == []
    await gw.around(FORUM_ID, 42, before=2, after=0)
    assert [c.get("reverse") for c in stub.iter_calls if "ids" not in c] == [None]


async def test_around_missing_target(gw: TelethonGateway):
    with pytest.raises(GatewayError, match="No message 42"):
        await gw.around(FORUM_ID, 42, before=1, after=1)


async def test_input_user_parsing(gw: TelethonGateway):
    stub = _stub(gw)
    nora = types.InputPeerUser(user_id=333, access_hash=1)
    assert gw._input_user(stub, None) is None
    assert gw._input_user(stub, "333") == nora
    assert gw._input_user(stub, "@NORA") == nora  # usernames are case-insensitive
    with pytest.raises(GatewayError, match="sender must be a user id or @username"):
        gw._input_user(stub, "Nora K")
    assert stub.requests == []  # resolved from what the session has seen, never the network


async def test_search_in_chat_builds_search_request(gw: TelethonGateway):
    stub = _stub(gw)
    stub.results.append(SimpleNamespace(messages=[_m(5, topic=7), _service(6)], users=[], chats=[]))
    hits = await gw.search_in_chat(
        FORUM_ID,
        "granite",
        topic=7,
        sender="333",
        since=date(2026, 10, 1),
        until=date(2026, 10, 2),
        limit=20,
    )
    request = stub.requests[0]
    assert isinstance(request, functions.messages.SearchRequest)
    assert (request.q, request.top_msg_id, request.limit) == ("granite", 7, 20)
    assert request.from_id == types.InputPeerUser(user_id=333, access_hash=1)
    assert request.min_date == datetime(2026, 10, 1, tzinfo=UTC)
    assert request.max_date == datetime(2026, 10, 3, tzinfo=UTC)
    assert [h.id for h in hits] == [5]
    assert hits[0].topic_id == 7


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
    assert [m.id for m in out.messages] == [3, 1]
    assert (out.next_before_id, out.stopped_early) == (None, False)  # the stream ended
    call = stub.iter_calls[-1]
    assert call["offset_date"] == datetime(2026, 10, 30, tzinfo=UTC)


async def test_flood_wait_becomes_busy(gw: TelethonGateway):
    stub = _stub(gw)
    stub.me_error = errors.FloodWaitError(request=None, capture=42)
    with pytest.raises(Busy) as caught:
        await gw.me()
    assert caught.value.seconds == 42
    assert gw._in_flight == 0


def _guard_state(gw: TelethonGateway) -> tuple[object, object]:
    """Read afresh: mypy keeps an attribute narrowed across an await."""
    return gw._guard, gw._guard_task


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
    assert _guard_state(gw) == (None, None)
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


@pytest.mark.parametrize("sender", ["@ghost", "999"])
async def test_unseen_sender_is_unknown_without_a_request(gw: TelethonGateway, sender: str):
    stub = _stub(gw)
    with pytest.raises(GatewayError) as caught:
        gw._input_user(stub, sender)
    assert str(caught.value) == UNSEEN_USER
    assert stub.requests == []


async def test_a_channel_number_is_not_a_sender(gw: TelethonGateway):
    stub = _stub(gw)
    news = _channel(4040, broadcast=True)
    stub.session.process_entities(types.contacts.ResolvedPeer(types.PeerChannel(4040), [news], []))
    with pytest.raises(GatewayError) as caught:
        gw._input_user(stub, "4040")
    assert str(caught.value) == UNSEEN_USER


async def test_mapping_bug_is_internal_error_with_traceback(
    gw: TelethonGateway, caplog: pytest.LogCaptureFixture
):
    broken = types.Message(id=9, peer_id=types.PeerChannel(1005), date=None, message="x")
    _stub(gw).messages["plain"] = [broken]
    caplog.set_level(logging.ERROR, logger="tgmcp.telegram.errors")
    with pytest.raises(GatewayError) as caught:
        await gw.history(FORUM_ID, topic=None, before_id=None, since=None, until=WHEN, limit=5)
    assert str(caught.value) == "Internal error in the Telegram gateway: ValidationError."
    record = next(r for r in caplog.records if r.message.startswith("internal gateway error"))
    assert record.message.startswith("internal gateway error ValidationError\n")
    assert "in history" in record.message  # the stack is there
    assert "input_value" not in record.message  # the exception message is not


async def test_history_fills_the_page_past_service_messages(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["plain"] = [_m(10), _service(9), _m(8), _service(7), _m(6), _m(5), _m(4)]
    out = await gw.history(FORUM_ID, topic=None, before_id=None, since=None, until=WHEN, limit=3)
    assert [m.id for m in out.messages] == [10, 8, 6]
    assert (out.next_before_id, out.stopped_early) == (6, False)  # a full page: continue below
    assert stub.iter_calls[-1]["limit"] == 12  # bounded: `None` would sleep 1 s per chunk


async def test_history_limit_bounds_the_stream(gw: TelethonGateway):
    await gw.history(FORUM_ID, topic=None, before_id=None, since=None, until=None, limit=100)
    assert _stub(gw).iter_calls[-1]["limit"] == 400


async def test_history_safety_stop_keeps_the_cursor(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["plain"] = [_m(40), *(_service(i) for i in range(39, 9, -1)), _m(9), _m(8)]
    out = await gw.history(FORUM_ID, topic=None, before_id=None, since=None, until=WHEN, limit=2)
    assert [m.id for m in out.messages] == [40]  # stopped after 2 * 4 raw messages
    assert out.stopped_early is True
    assert out.next_before_id == 33  # the last raw message scanned: the next page starts below


async def test_history_stops_at_since(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["plain"] = [_m(3), _m(2)]
    out = await gw.history(
        FORUM_ID,
        topic=None,
        before_id=None,
        since=datetime(2030, 1, 1, tzinfo=UTC),
        until=WHEN,
        limit=5,
    )
    assert (out.messages, out.next_before_id, out.stopped_early) == ([], None, False)


async def test_search_global_fills_the_page_and_keeps_since(gw: TelethonGateway):
    stub = _stub(gw)
    stub.messages["plain"] = [_m(10), _service(9), _m(8), _m(7)]
    out = await gw.search_global("x", since=None, until=None, limit=2)
    assert [m.id for m in out] == [10, 8]
    assert stub.iter_calls[-1]["limit"] == 8
    later = await gw.search_global("x", since=date(2030, 1, 1), until=None, limit=2)
    assert later == []


class LoginClient:
    """Stands in for TelegramClient in interactive_login; a successful start stores a key."""

    error: BaseException | None = None
    disconnects = 0
    had_key: bool | None = None
    home: Path | None = None

    def __init__(self, session: StringSession, api_id: int, api_hash: str, **_kw: Any) -> None:
        assert isinstance(session, StringSession)  # in memory: no SQLite file
        self.session = session

    async def start(self) -> None:
        LoginClient.had_key = self.session.auth_key is not None
        assert LoginClient.home is not None
        written = [f for f in LoginClient.home.glob("*.session*") if f.name[:9] != "telegram."]
        assert written == []  # nothing on disk mid-login except an old file the test planted
        if LoginClient.error is not None:
            raise LoginClient.error
        if self.session.auth_key is None:
            self.session.set_dc(2, "149.154.167.51", 443)
            self.session.auth_key = AuthKey(b"" * 256)

    async def get_me(self) -> Any:
        return types.User(id=100, first_name="Alex", username="alex", access_hash=1)

    async def disconnect(self) -> None:
        LoginClient.disconnects += 1
        self.session.close()


@pytest.fixture
def login_settings(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(auth, "AuthClient", LoginClient)
    monkeypatch.setattr(config, "restrict_to_owner", lambda path: None)
    LoginClient.error = None
    LoginClient.disconnects = 0
    LoginClient.had_key = None
    LoginClient.home = tmp_path
    return Settings(api_id=1, api_hash=None, hidden_chats=(), expected_user=None, home=tmp_path)


def _files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def _legacy_session(path: Path) -> None:
    """What the old version left: Telethon's SQLite file with a login key."""
    session = SQLiteSession(str(path))
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(b"" * 256)
    session.save()
    session.close()


async def test_login_maps_wrong_api_key(login_settings: Settings):
    LoginClient.error = errors.ApiIdInvalidError(request=None)
    with pytest.raises(ApiKeyError, match=r"tgmcp login --reset"):
        await auth.interactive_login(login_settings, "h")
    assert LoginClient.disconnects == 1
    assert _files(login_settings.home) == []


async def test_login_maps_three_wrong_codes(login_settings: Settings):
    LoginClient.error = RuntimeError("3 consecutive sign-in attempts failed. Aborting")
    with pytest.raises(GatewayError) as caught:
        await auth.interactive_login(login_settings, "h")
    assert str(caught.value) == "The code was wrong three times: run `tgmcp login` again."
    assert _files(login_settings.home) == []


@pytest.mark.parametrize(
    "error", [errors.AuthKeyDuplicatedError(request=None), errors.AuthKeyUnregisteredError(None)]
)
async def test_login_removes_a_dead_session(login_settings: Settings, error: Exception):
    _legacy_session(login_settings.legacy_session_path)
    secrets.write_secrets(login_settings.secrets_path, "h", make_session_string())
    LoginClient.error = error
    with pytest.raises(GatewayError, match="it was removed"):
        await auth.interactive_login(login_settings, "h")
    assert _files(login_settings.home) == []
    assert LoginClient.disconnects == 1


async def test_login_writes_encrypted_secrets_and_no_session_file(login_settings: Settings):
    account = await auth.interactive_login(login_settings, "the-hash")
    assert (account.name, account.username) == ("Alex", "alex")
    assert LoginClient.had_key is False
    assert _files(login_settings.home) == ["secrets.bin"]
    saved = secrets.read_secrets(login_settings.secrets_path)
    assert saved.api_hash == "the-hash"
    assert StringSession(saved.session).auth_key.key == b"" * 256


async def test_login_reuses_the_saved_login(login_settings: Settings):
    old = make_session_string()
    secrets.write_secrets(login_settings.secrets_path, "h", old)
    await auth.interactive_login(login_settings, "h")
    assert LoginClient.had_key is True
    assert secrets.read_secrets(login_settings.secrets_path).session == old


async def test_login_moves_the_old_session_file(login_settings: Settings):
    _legacy_session(login_settings.legacy_session_path)
    await auth.interactive_login(login_settings, "h")
    assert LoginClient.had_key is True
    saved = secrets.read_secrets(login_settings.secrets_path)
    assert StringSession(saved.session).auth_key.key == b"" * 256
    assert _files(login_settings.home) == ["secrets.bin", "telegram.session"]  # CLI removes it


async def test_login_without_reuse_starts_fresh(login_settings: Settings):
    _legacy_session(login_settings.legacy_session_path)
    secrets.write_secrets(login_settings.secrets_path, "h", make_session_string())
    await auth.interactive_login(login_settings, "h", reuse=False)
    assert LoginClient.had_key is False
    saved = secrets.read_secrets(login_settings.secrets_path)
    assert StringSession(saved.session).auth_key.key == b"" * 256


async def test_login_whose_permissions_fail_says_so(login_settings: Settings, monkeypatch):
    def icacls_fails(path: Path) -> None:
        raise subprocess.CalledProcessError(1, ["icacls"])

    monkeypatch.setattr(config, "restrict_to_owner", icacls_fails)
    with pytest.raises(GatewayError, match="file permissions could not be set"):
        await auth.interactive_login(login_settings, "h")


async def test_login_without_a_terminal_says_so(login_settings: Settings):
    LoginClient.error = EOFError()  # Telethon's input() on a closed stdin
    with pytest.raises(GatewayError) as caught:
        await auth.interactive_login(login_settings, "h")
    assert str(caught.value) == NO_TERMINAL
    assert _files(login_settings.home) == []


async def test_login_blocked_by_the_guard_is_not_three_wrong_codes(login_settings: Settings):
    LoginClient.error = ReadOnlyViolation("SendMessageRequest")  # a RuntimeError too
    with pytest.raises(GatewayError, match="refused SendMessageRequest"):
        await auth.interactive_login(login_settings, "h")


async def test_interrupted_login_leaves_no_temporary_file(login_settings: Settings):
    LoginClient.error = KeyboardInterrupt()  # Ctrl-C at the code prompt
    with pytest.raises(KeyboardInterrupt):
        await auth.interactive_login(login_settings, "h")
    assert _files(login_settings.home) == []


async def test_login_that_cannot_save_says_so(login_settings: Settings, monkeypatch):
    def full_disk(path: Path, api_hash: str, session: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(auth, "write_secrets", full_disk)
    with pytest.raises(GatewayError, match="Could not save the login"):
        await auth.interactive_login(login_settings, "h")
    assert _files(login_settings.home) == []


async def test_login_without_a_key_is_not_saved(login_settings: Settings, monkeypatch):
    async def no_key(self: LoginClient) -> None:
        return None

    monkeypatch.setattr(LoginClient, "start", no_key)
    with pytest.raises(GatewayError, match="did not finish"):
        await auth.interactive_login(login_settings, "h")
    assert _files(login_settings.home) == []
