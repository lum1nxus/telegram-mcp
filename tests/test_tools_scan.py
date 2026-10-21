# ruff: noqa: RUF001 - Russian seed text has one-letter Cyrillic words
import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from tests.conftest import _NOW, Call
from tests.factories import make_chat, make_message
from tests.fake_gateway import FakeGateway
from tgmcp import server
from tgmcp.config import Settings
from tgmcp.gateway import Busy
from tgmcp.server import DATA_SENTENCE, build_server, guarded

PLOT = -3001


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "PAGE_PAUSE_S", 0.0)


def _data(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return dict(result.structured_content or {})


def _text(result: Any) -> str:
    return str(result.content[0].text)


def _chat_with(
    texts: list[str], *, forum: bool = False, topics: dict[int, int] | None = None
) -> FakeGateway:
    """One chat "Plot". Message n (1-based, oldest first) is one hour apart; the newest is last.

    Odd ids are from user 555 (Dana), even ids from user 111 (Nora); both are in the dialogs.
    """
    messages = [
        make_message(
            n,
            chat_id=PLOT,
            date=_NOW - timedelta(hours=len(texts) - n),
            sender_id=555 if n % 2 else 111,
            topic_id=(topics or {}).get(n),
            text=text,
        )
        for n, text in enumerate(texts, start=1)
    ]
    chats = [
        make_chat(PLOT, title="Plot", is_forum=forum),
        make_chat(111, title="Nora", kind="user", username="nora"),
        make_chat(555, title="Dana", kind="user", username="dana"),
    ]
    return FakeGateway(chats, {PLOT: messages})


def _big(count: int = 450, hit_every: int = 0) -> FakeGateway:
    texts = [
        "минитрактор у Tomka" if hit_every and n % hit_every == 0 else f"message {n}"
        for n in range(1, count + 1)
    ]
    return _chat_with(texts)


async def _scan(gateway: FakeGateway, settings: Settings, **arguments: Any) -> Any:
    async with Client(build_server(gateway, settings)) as client:
        return await client.call_tool("scan_chat", {"chat": "Plot", **arguments})


async def test_fragment_finds_compound_that_search_misses(call: Call) -> None:
    exact = _data(await call("search_messages", {"query": "трактор", "chat": "Garden Club"}))
    assert exact["hits"] == []
    data = _data(await call("scan_chat", {"chat": "Garden Club", "query": "трактор"}))
    assert [h["id"] for h in data["hits"]] == [1]
    assert data["mode"] == "fragment"
    assert data["scanned"] == 5
    assert data["reached_start"] is True
    assert data["stopped_by"] == "start"
    assert data["chat"]["title"] == "Garden Club"
    assert data["note"] == "Scanned all 5 messages of 'Garden Club'; the whole chat was covered."
    assert data["newest_scanned"] > data["oldest_scanned"]
    assert data["elapsed_s"] >= 0


async def test_words_and_whole_word_typo_tolerance(call: Call) -> None:
    data = _data(
        await call(
            "scan_chat", {"chat": "Garden Club", "query": "поделился теплицей", "mode": "words"}
        )
    )
    assert [h["id"] for h in data["hits"]] == [5]
    data = _data(
        await call(
            "scan_chat",
            {"chat": "Garden Club", "query": "тепплицей", "mode": "words", "tolerance": 1},
        )
    )
    assert [h["id"] for h in data["hits"]] == [5]


async def test_tolerance_one_finds_typo_of_a_whole_word(settings: Settings) -> None:
    gateway = _chat_with(["трактор у Tomka", "kosiarka"])
    assert [
        h["id"] for h in _data(await _scan(gateway, settings, query="трактр", tolerance=1))["hits"]
    ] == [1]
    assert _data(await _scan(gateway, settings, query="трактр"))["hits"] == []


async def test_regex_mode(call: Call) -> None:
    data = _data(
        await call("scan_chat", {"chat": "Garden Club", "query": "Zielon.? firma", "mode": "regex"})
    )
    assert [h["id"] for h in data["hits"]] == [3]


async def test_refused_regex_is_error_before_any_history_call(
    call: Call, seed: FakeGateway
) -> None:
    result = await call("scan_chat", {"chat": "Garden Club", "query": "(a+)+b", "mode": "regex"})
    assert result.is_error
    assert "could run forever" in _text(result)
    result = await call("scan_chat", {"chat": "Garden Club", "query": "x" * 201, "mode": "regex"})
    assert result.is_error
    assert "200 characters" in _text(result)
    assert "history" not in seed.calls


async def test_bounds_are_refused(call: Call) -> None:
    for extra in (
        {"tolerance": 3},
        {"max_messages": 99},
        {"max_messages": 20_001},
        {"limit": 0},
        {"limit": 101},
    ):
        result = await call("scan_chat", {"chat": "Garden Club", "query": "x", **extra})
        assert result.is_error


async def test_max_messages_stops_with_oldest_scanned(settings: Settings) -> None:
    gateway = _big()
    data = _data(await _scan(gateway, settings, query="nothing here", max_messages=200))
    assert data["scanned"] == 200
    assert data["stopped_by"] == "max_messages"
    assert data["reached_start"] is False
    assert _when(data["oldest_scanned"]) == _NOW - timedelta(hours=199)
    assert _when(data["newest_scanned"]) == _NOW
    assert data["note"] == (
        "Scanned the last 200 messages of 'Plot' back to "
        f"{_NOW - timedelta(hours=199):%Y-%m-%d}; the chat continues further back."
    )


async def test_max_messages_is_never_exceeded(settings: Settings) -> None:
    gateway = _big()
    assert _data(await _scan(gateway, settings, query="zzz", max_messages=150))["scanned"] == 150
    assert gateway.calls.count("history") == 2


async def test_whole_450_message_chat_is_paged(settings: Settings) -> None:
    gateway = _big(hit_every=100)
    data = _data(await _scan(gateway, settings, query="трактор"))
    assert data["scanned"] == 450
    assert data["stopped_by"] == "start"
    assert [h["id"] for h in data["hits"]] == [400, 300, 200, 100]
    assert gateway.calls.count("history") == 5


async def test_since_inside_the_chat(settings: Settings) -> None:
    gateway = _big(count=10)
    since = _NOW - timedelta(hours=4, minutes=30)  # messages 6..10
    data = _data(await _scan(gateway, settings, query="message", since=since.isoformat()))
    assert data["stopped_by"] == "since"
    assert data["scanned"] == 5
    assert data["reached_start"] is False
    assert data["note"] == f"Scanned 5 messages of 'Plot' from {since:%Y-%m-%d} on."


async def test_limit_keeps_the_newest_hits(settings: Settings) -> None:
    gateway = _chat_with(
        ["greenhouse", "x", "greenhouse", "greenhouse", "y", "greenhouse", "greenhouse"]
    )
    data = _data(await _scan(gateway, settings, query="greenhouse", limit=2))
    assert [h["id"] for h in data["hits"]] == [7, 6]
    assert data["stopped_by"] == "limit"
    assert data["scanned"] == 2
    assert data["truncated"] is True
    assert data["note"].startswith("Found the newest 2 hits within the last 2 messages of 'Plot'")


async def test_sender_by_username_and_by_id(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse"] * 6)
    data = _data(await _scan(gateway, settings, query="greenhouse", sender="@nora"))
    assert [h["id"] for h in data["hits"]] == [6, 4, 2]
    assert data["scanned"] == 6  # coverage counts every message, hits only the sender's
    data = _data(await _scan(gateway, settings, query="greenhouse", sender="555"))
    assert [h["id"] for h in data["hits"]] == [5, 3, 1]


async def test_numeric_sender_outside_the_dialogs_filters_as_is(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse"] * 4)
    gateway.messages[PLOT][1] = gateway.messages[PLOT][1].model_copy(update={"sender_id": 777})
    data = _data(await _scan(gateway, settings, query="greenhouse", sender="777"))
    assert [h["id"] for h in data["hits"]] == [2]


async def test_unknown_username_is_error_before_any_history_call(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse"])
    for sender in ("@ghost", "Dana"):
        result = await _scan(gateway, settings, query="greenhouse", sender=sender)
        assert result.is_error
        assert "sender" in _text(result).lower()
    assert "history" not in gateway.calls


async def test_topic_filter_and_plain_group(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse"] * 4, forum=True, topics={1: 1, 2: 7, 3: 7, 4: 1})
    data = _data(await _scan(gateway, settings, query="greenhouse", topic=7))
    assert [h["id"] for h in data["hits"]] == [3, 2]
    result = await _scan(_chat_with(["greenhouse"]), settings, query="greenhouse", topic=7)
    assert result.is_error
    assert "is not a forum group" in _text(result)


async def test_hidden_chat_is_not_found(call: Call, seed: FakeGateway) -> None:
    result = await call("scan_chat", {"chat": "-1007", "query": "granite"})
    assert result.is_error
    assert "No chat matches" in _text(result)
    assert "history" not in seed.calls


async def test_since_after_until_is_refused(call: Call) -> None:
    result = await call(
        "scan_chat",
        {
            "chat": "Garden Club",
            "query": "x",
            "since": "2026-10-02T00:00:00",
            "until": "2026-10-01T00:00:00",
        },
    )
    assert result.is_error
    assert "since must not be after until" in _text(result)


async def test_busy_on_first_page_is_error(settings: Settings) -> None:
    gateway = _big()
    gateway.raise_next = Busy(30)
    result = await _scan(gateway, settings, query="трактор")
    assert result.is_error
    assert "Telegram asks to wait 30 seconds" in _text(result)


async def test_busy_on_second_page_returns_flood(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _big(hit_every=50)
    original = gateway.history
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Busy(42)
        return await original(*args, **kwargs)

    monkeypatch.setattr(gateway, "history", flaky)
    data = _data(await _scan(gateway, settings, query="трактор"))
    assert data["stopped_by"] == "flood"
    assert data["scanned"] == 100
    assert [h["id"] for h in data["hits"]] == [450, 400]
    assert data["note"] == (
        f"Scanned 100 messages of 'Plot' back to {_NOW - timedelta(hours=99):%Y-%m-%d}; "
        "Telegram asked to wait 42 seconds, so the scan stopped there; do not retry sooner."
    )


def _ticking(jump_after: int) -> Callable[[], float]:
    """A clock that reads 0 for its first `jump_after` calls and 61 s afterwards."""
    calls = 0

    def tick() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls <= jump_after else 61.0

    return tick


async def test_time_budget_after_the_first_page(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _big()
    # call 1 starts the budget, call 2 checks page 1; its 100 messages are calls 3..102
    monkeypatch.setattr(server, "clock", _ticking(101))
    data = _data(await _scan(gateway, settings, query="zzz"))
    assert data["stopped_by"] == "time"
    assert data["scanned"] == 100
    assert gateway.calls.count("history") == 1
    assert data["note"] == (
        f"Scanned 100 messages of 'Plot' back to {_NOW - timedelta(hours=99):%Y-%m-%d} "
        "before the 50-second budget ran out; call again with since/until to continue."
    )


async def test_time_budget_is_checked_after_every_message(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _big()
    monkeypatch.setattr(server, "clock", _ticking(11))  # the 10th message's check reads 61
    data = _data(await _scan(gateway, settings, query="zzz"))
    assert data["scanned"] == 10
    assert data["stopped_by"] == "time"
    assert _when(data["oldest_scanned"]) == _NOW - timedelta(hours=9)


async def test_budget_cuts_long_hits(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse " + "y" * 3000] * 40)
    data = _data(await _scan(gateway, settings, query="greenhouse", limit=40))
    assert data["truncated"] is True
    assert 0 < len(data["hits"]) < 40
    assert all(len(h["text"]) <= 2000 for h in data["hits"])
    assert data["hits"][0]["text_truncated"] is True


async def test_empty_chat_is_start(settings: Settings) -> None:
    data = _data(await _scan(_chat_with([]), settings, query="x"))
    assert (data["scanned"], data["stopped_by"], data["oldest_scanned"]) == (0, "start", None)


async def test_annotations_description_and_seven_tools(client: Client) -> None:
    async with client as connected:
        tools = {t.name: t for t in (await connected.list_tools()).tools}
    assert len(tools) == 7
    scan = tools["scan_chat"]
    assert scan.annotations is not None
    assert scan.annotations.read_only_hint is True
    assert scan.annotations.open_world_hint is False
    description = scan.description or ""
    assert description.endswith(DATA_SENTENCE)
    for phrase in (
        "IGNORECASE",
        "nested quantifiers",
        "back-references",
        "compound word",
        "empty branches",
        "conditionals",
        "first 1024 characters",
        "first 256",
    ):
        assert phrase in description
    assert scan.input_schema["properties"]["max_messages"]["default"] == 3000


async def test_guarded_takes_its_own_timeout() -> None:
    @guarded("scan_chat", timeout_s=0.01)
    async def slow() -> None:
        await asyncio.sleep(0.2)

    with pytest.raises(ToolError, match=r"scan_chat took longer than 0\.01 seconds"):
        await slow()


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def test_real_time_budget_returns_hits_instead_of_an_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _big(hit_every=50)
    original = gateway.history

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.15)
        return await original(*args, **kwargs)

    monkeypatch.setattr(gateway, "history", slow)
    monkeypatch.setattr(server, "SCAN_BUDGET_S", 0.3)
    monkeypatch.setattr(server, "SCAN_TIMEOUT_S", 2.0)
    started = time.monotonic()
    data = _data(await _scan(gateway, settings, query="трактор"))
    assert time.monotonic() - started < 2
    assert data["stopped_by"] == "time"
    assert 0 < gateway.calls.count("history") < 5
    assert data["hits"]
    assert data["scanned"] >= 100


async def test_pause_only_between_pages(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "PAGE_PAUSE_S", 0.0123)
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def spy(delay: float, *args: Any) -> None:
        if delay == 0.0123:
            pauses.append(delay)
        await real_sleep(0, *args)

    monkeypatch.setattr(asyncio, "sleep", spy)
    gateway = _big(count=450)
    _data(await _scan(gateway, settings, query="zzz", max_messages=500))
    assert gateway.calls.count("history") == 5
    assert len(pauses) == 4


async def test_pause_also_between_pages_of_service_messages(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "PAGE_PAUSE_S", 0.0123)
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def spy(delay: float, *args: Any) -> None:
        if delay == 0.0123:
            pauses.append(delay)
        await real_sleep(0, *args)

    monkeypatch.setattr(asyncio, "sleep", spy)
    gateway = _big(count=1000, hit_every=1)
    gateway.service[PLOT] = set(range(201, 1001))  # the newest 800 are service messages
    data = _data(await _scan(gateway, settings, query="трактор"))
    pages = gateway.calls.count("history")
    assert pages >= 3
    assert len(pauses) == pages - 1
    assert data["stopped_by"] == "limit"


async def test_stopping_on_the_last_message_of_the_chat_reports_start(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse", "x", "y"])
    data = _data(await _scan(gateway, settings, query="greenhouse", limit=1))
    assert data["stopped_by"] == "start"
    assert data["reached_start"] is True
    assert data["truncated"] is False
    assert [h["id"] for h in data["hits"]] == [1]


async def test_note_under_until_names_the_scope(settings: Settings) -> None:
    gateway = _chat_with([f"greenhouse {n}" for n in range(1, 11)])
    until = _NOW - timedelta(hours=3)
    data = _data(await _scan(gateway, settings, query="greenhouse", until=until.isoformat()))
    assert data["stopped_by"] == "start"
    assert data["reached_start"] is True
    assert f"up to {until:%Y-%m-%d}" in data["note"]
    assert "whole chat" not in data["note"]
    assert data["note"].endswith("everything in that scope was covered.")


async def test_note_under_topic_names_the_scope(settings: Settings) -> None:
    gateway = _chat_with(["greenhouse"] * 4, forum=True, topics={1: 1, 2: 7, 3: 7, 4: 1})
    data = _data(await _scan(gateway, settings, query="greenhouse", topic=7))
    assert "in topic 7" in data["note"]
    assert "whole chat" not in data["note"]
    until = _NOW - timedelta(hours=1)
    data = _data(
        await _scan(gateway, settings, query="greenhouse", topic=7, until=until.isoformat())
    )
    assert f"in topic 7 up to {until:%Y-%m-%d}" in data["note"]


async def test_note_under_scope_says_newest_for_max_messages(settings: Settings) -> None:
    gateway = _big()
    data = _data(
        await _scan(gateway, settings, query="zzz", max_messages=200, until=_NOW.isoformat())
    )
    assert data["stopped_by"] == "max_messages"
    assert data["note"].startswith(
        f"Scanned the 200 newest messages of 'Plot' up to {_NOW:%Y-%m-%d}"
    )


@pytest.mark.parametrize("mode", ["fragment", "words", "regex"])
async def test_blank_query_is_error_before_any_history_call(settings: Settings, mode: str) -> None:
    gateway = _chat_with(["greenhouse"])
    for query in ("", "   "):
        result = await _scan(gateway, settings, query=query, mode=mode)
        assert result.is_error
        assert "query must not be blank." in _text(result)
    assert "history" not in gateway.calls
