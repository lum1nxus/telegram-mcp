"""The only door to Telegram: a read-only Protocol and the errors it raises."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from tgmcp.models import Account, Chat, Message, Topic


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
