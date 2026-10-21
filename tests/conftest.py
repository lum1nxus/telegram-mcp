from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from tests.factories import make_chat, make_message, make_topic
from tests.fake_gateway import FakeGateway
from tgmcp.config import Settings
from tgmcp.models import Message
from tgmcp.server import build_server

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _leasing_messages() -> list[Message]:
    out: list[Message] = []
    for n in range(1, 121):
        fields: dict[str, object] = {
            "chat_id": -1001,
            "date": _NOW - timedelta(hours=120 - n),
            "text": f"message {n}",
        }
        if n % 10:
            fields.update(sender_id=333, sender_name="Ola")
        if n == 50:
            fields["text"] = "Erste dał lizing bez problemu"
        if n == 60:
            fields["text"] = "VEHIS odmówił"
        if n == 120:
            fields["text"] = "x" * 4096
        out.append(make_message(n, **fields))
    return out


@pytest.fixture
def seed() -> FakeGateway:
    chats = [
        make_chat(111, title="Anna", kind="user", username="anna"),
        make_chat(-1001, title="Leasing PL", members=3),
        make_chat(-1002, title="Leasing Poland"),
        make_chat(
            -1003,
            title="Auto News",
            kind="channel",
            username="autonews",
            discussion_chat_id=-1004,
        ),
        make_chat(-1004, title="Auto News chat"),
        make_chat(-1005, title="Expats WAW", is_forum=True),
        make_chat(-1006, title="Old", archived=True),
        make_chat(-1007, title="Private"),
    ]
    forum = [
        make_message(
            n, chat_id=-1005, topic_id=1 if n <= 5 else 7, date=_NOW - timedelta(hours=10 - n)
        )
        for n in range(1, 11)
    ]
    return FakeGateway(
        chats,
        {-1001: _leasing_messages(), -1005: forum},
        {-1005: [make_topic(1, title="General"), make_topic(7, title="Cars")]},
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_id=1, api_hash="a", hidden_chats=("-1007",), expected_user=None, home=tmp_path
    )


Call = Callable[[str, dict[str, Any]], Awaitable[CallToolResult]]


@pytest.fixture
def make_client(seed: FakeGateway, settings: Settings) -> Callable[[], Client]:
    """Factory of unentered Clients.

    An async-generator fixture cannot hold `async with Client`: anyio requires the
    context to exit in the task that entered it, and pytest runs teardown elsewhere.
    Tests use the `call` fixture or enter a client themselves.
    """
    return lambda: Client(build_server(lambda _s: seed, settings))


@pytest.fixture
def client(make_client: Callable[[], Client]) -> Client:
    return make_client()


@pytest.fixture
def call(make_client: Callable[[], Client]) -> Call:
    async def run(name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        async with make_client() as connected:
            return await connected.call_tool(name, arguments or {})

    return run
