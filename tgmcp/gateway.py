"""The read-only door to Telegram as the server sees it: the Protocol, its errors and helpers.

Nothing here imports Telethon. The Telethon side lives in the `tgmcp.telegram` package.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Protocol

from tgmcp.models import Account, Chat, Message, Topic

IDLE_SECONDS = 600
# A page skips service messages; it gives up after this many raw messages per wanted one.
RAW_PER_MESSAGE = 4


class GatewayError(Exception):
    """A sentence for the model."""


class NotLoggedIn(GatewayError):  # noqa: N818 - name fixed by the interface
    def __init__(self, message: str = "Not logged in: run `tgmcp login` in a terminal.") -> None:
        super().__init__(message)


MIGRATE = (
    "Old clear-text files found: run `tgmcp login` once to move them into the encrypted store."
)


class ApiKeyError(GatewayError):
    """Telegram rejected the API id or hash, so the saved ones are not worth keeping."""


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


# --- Dates ----------------------------------------------------------------------------------


def aware(dt: datetime | None) -> datetime | None:
    """A naive datetime is taken as UTC."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def utc_day(day: date) -> date:
    """The UTC calendar day of a date or datetime (a naive datetime is taken as UTC)."""
    if isinstance(day, datetime):
        moment = aware(day)
        assert moment is not None
        return moment.astimezone(UTC).date()
    return day


def day_start(day: date | None) -> datetime | None:
    return datetime.combine(utc_day(day), dtime.min, tzinfo=UTC) if day else None


def day_end(day: date | None) -> datetime | None:
    """Exclusive end: the first instant after `day`."""
    if not day:
        return None
    return datetime.combine(utc_day(day) + timedelta(days=1), dtime.min, tzinfo=UTC)
