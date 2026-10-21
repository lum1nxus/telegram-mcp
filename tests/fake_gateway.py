"""In-memory Gateway used by every tool test."""

import re
from datetime import date, datetime

from tgmcp.gateway import GatewayError, History
from tgmcp.models import Account, Chat, Message, Topic


def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"\w+", text, re.UNICODE)]


def _matches(text: str, query: str) -> bool:
    words = _words(text)
    return all(any(w.startswith(q) for w in words) for q in _words(query))


class FakeGateway:
    def __init__(
        self,
        chats: list[Chat],
        messages: dict[int, list[Message]],
        topics: dict[int, list[Topic]] | None = None,
    ) -> None:
        self.chats = chats
        self.messages = messages
        self._topics = topics or {}
        self.raise_next: GatewayError | None = None
        self.calls: list[str] = []

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error

    async def me(self) -> Account:
        self._enter("me")
        return Account(id=100, name="Alex", username="alex")

    async def dialogs(self) -> list[Chat]:
        self._enter("dialogs")
        return list(self.chats)

    async def chat_details(self, chat_id: int) -> Chat:
        self._enter("chat_details")
        for chat in self.chats:
            if chat.id == chat_id:
                return chat
        raise GatewayError(f"No chat {chat_id}.")

    async def topics(self, chat_id: int, limit: int) -> list[Topic]:
        self._enter("topics")
        return list(self._topics.get(chat_id, []))[:limit]

    async def history(
        self,
        chat_id: int,
        *,
        topic: int | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> History:
        self._enter("history")
        found = [
            m
            for m in self.messages.get(chat_id, [])
            if (topic is None or m.topic_id == topic)
            and (before_id is None or m.id < before_id)
            and (since is None or m.date >= since)
            and (until is None or m.date <= until)
        ]
        found.sort(key=lambda m: m.id, reverse=True)
        page = found[:limit]
        return History(page, page[-1].id if len(page) == limit else None)

    def _filter(
        self, chat_id: int, query: str, since: date | None, until: date | None
    ) -> list[Message]:
        return [
            m
            for m in self.messages.get(chat_id, [])
            if _matches(m.text, query)
            and (since is None or m.date.date() >= since)
            and (until is None or m.date.date() <= until)
        ]

    def _sender_id(self, sender: str | None) -> int | None:
        if sender is None:
            return None
        if not sender.startswith("@"):
            return int(sender)
        for chat in self.chats:
            if chat.kind == "user" and (chat.username or "").lower() == sender[1:].lower():
                return chat.id
        raise GatewayError(f"No user {sender} in your chats.")

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
        self._enter("search_in_chat")
        if sender is not None and not re.fullmatch(r"@\w+|\d+", sender):
            raise GatewayError("sender must be a user id or @username")
        sender_id = self._sender_id(sender)
        found = [
            m
            for m in self._filter(chat_id, query, since, until)
            if (topic is None or m.topic_id == topic)
            and (sender_id is None or m.sender_id == sender_id)
        ]
        found.sort(key=lambda m: m.id, reverse=True)
        return found[:limit]

    async def search_global(
        self, query: str, *, since: date | None, until: date | None, limit: int
    ) -> list[Message]:
        self._enter("search_global")
        found = [m for chat_id in self.messages for m in self._filter(chat_id, query, since, until)]
        found.sort(key=lambda m: m.date, reverse=True)
        return found[:limit]

    async def around(
        self, chat_id: int, message_id: int, *, before: int, after: int
    ) -> tuple[list[Message], Message, list[Message]]:
        self._enter("around")
        ordered = sorted(self.messages.get(chat_id, []), key=lambda m: m.id)
        index = next((i for i, m in enumerate(ordered) if m.id == message_id), None)
        if index is None:
            raise GatewayError(f"No message {message_id} in that chat.")
        return (
            ordered[max(0, index - before) : index],
            ordered[index],
            ordered[index + 1 : index + 1 + after],
        )

    async def close(self) -> None:
        self._enter("close")
