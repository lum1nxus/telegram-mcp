from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult

from tgmcp.config import Settings
from tgmcp.host.benchdata import NOW, FakeGateway, build_seed
from tgmcp.server import build_server

_NOW = NOW  # tests import the clock from here


@pytest.fixture
def seed() -> FakeGateway:
    return build_seed()


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
    return lambda: Client(build_server(seed, settings))


@pytest.fixture
def client(make_client: Callable[[], Client]) -> Client:
    return make_client()


@pytest.fixture
def call(make_client: Callable[[], Client]) -> Call:
    async def run(name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        async with make_client() as connected:
            return await connected.call_tool(name, arguments or {})

    return run
