from datetime import timedelta
from typing import Any

from mcp import Client

from tests.conftest import _NOW, Call
from tests.factories import make_chat, make_message
from tests.fake_gateway import FakeGateway
from tgmcp.config import Settings
from tgmcp.models import SEARCH_NOTE
from tgmcp.server import build_server


def _text(result: Any) -> str:
    return str(result.content[0].text)


async def test_search_in_chat_finds_prefix_and_sets_note(call: Call) -> None:
    result = await call("search_messages", {"query": "granite", "chat": "Hiking Club"})
    assert not result.is_error
    data = result.structured_content or {}
    assert [h["id"] for h in data["hits"]] == [50]
    assert data["note"] == SEARCH_NOTE
    assert data["scope"] == "Hiking Club"


async def test_global_search_drops_hidden_chat_hits(call: Call, seed: FakeGateway) -> None:
    assert any(m.chat_id == -1007 for m in seed.messages[-1007])
    result = await call("search_messages", {"query": "granite"})
    assert not result.is_error
    data = result.structured_content or {}
    assert [h["chat_id"] for h in data["hits"]] == [-1001]
    assert data["scope"] == "all chats"
    assert "-1007" not in _text(result)


async def test_search_needs_query_or_chat(call: Call) -> None:
    result = await call("search_messages", {"query": ""})
    assert result.is_error
    assert "needs a query" in _text(result)
    result = await call("search_messages", {"query": "x", "sender": "@anna"})
    assert result.is_error
    assert "need a chat" in _text(result)


async def test_get_messages_pages_with_cursor(call: Call) -> None:
    result = await call("get_messages", {"chat": "-1001", "limit": 50})
    data = result.structured_content or {}
    assert data["next_before_id"] == 71
    result = await call("get_messages", {"chat": "-1001", "limit": 50, "before_id": 71})
    data = result.structured_content or {}
    assert [m["id"] for m in data["messages"]] == list(range(70, 20, -1))


async def test_long_message_is_capped_and_budget_truncates(call: Call, settings: Settings) -> None:
    result = await call("get_messages", {"chat": "-1001", "limit": 5})
    top = (result.structured_content or {})["messages"][0]
    assert top["id"] == 120
    assert len(top["text"]) == 2000
    assert top["text_truncated"] is True

    big = FakeGateway(
        [make_chat(-2001, title="Big")],
        {
            -2001: [
                make_message(n, chat_id=-2001, date=_NOW - timedelta(minutes=n), text="y" * 1500)
                for n in range(1, 101)
            ]
        },
    )
    async with Client(build_server(lambda _s: big, settings)) as client:
        result = await client.call_tool("get_messages", {"chat": "-2001", "limit": 100})
    data = result.structured_content or {}
    assert data["truncated"] is True
    assert len(data["messages"]) < 100


async def test_context_full_text_when_zero_window(call: Call) -> None:
    result = await call(
        "get_message_context", {"chat": "-1001", "message_id": 120, "before": 0, "after": 0}
    )
    data = result.structured_content or {}
    assert len(data["target"]["text"]) == 4096
    result = await call("get_message_context", {"chat": "-1001", "message_id": 120})
    data = result.structured_content or {}
    assert len(data["target"]["text"]) == 2000
    assert len(data["before"]) == 10


async def test_topic_filters_forum_history(call: Call) -> None:
    result = await call("get_messages", {"chat": "-1005", "topic": 7})
    messages = (result.structured_content or {})["messages"]
    assert messages
    assert {m["topic_id"] for m in messages} == {7}


async def test_date_range_search(call: Call) -> None:
    day = (_NOW - timedelta(hours=70)).date().isoformat()
    result = await call(
        "search_messages", {"query": "granite", "chat": "-1001", "since": day, "until": day}
    )
    data = result.structured_content or {}
    assert [h["id"] for h in data["hits"]] == [50]
    assert data["since"] == day
