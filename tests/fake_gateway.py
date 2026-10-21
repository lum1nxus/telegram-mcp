"""In-memory Gateway used by every tool test.

It follows the live gateway where the tools can tell the difference: `until` of a history is
exclusive (Telegram's `offset_date`), service messages (ids in `service`) are skipped and a long
run of them stops a page early with a cursor, the context of a forum message stays in its
topic, and `sender` resolves only users the session has seen (user dialogs and the senders of
messages already returned), with the live gateway's sentence.
"""

import re
from datetime import date, datetime

from tgmcp.gateway import RAW_PER_MESSAGE, UNSEEN_USER, GatewayError, History
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
        self.service: dict[int, set[int]] = {}  # chat id -> ids of service messages
        self.raise_next: GatewayError | None = None
        self.calls: list[str] = []
        self._seen_users: set[int] = set()

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error

    def _plain(self, chat_id: int, m: Message) -> bool:
        return m.id not in self.service.get(chat_id, set())

    def _seen(self, found: list[Message]) -> list[Message]:
        self._seen_users |= {m.sender_id for m in found if m.sender_id is not None}
        return found

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
        stream = sorted(
            (
                m
                for m in self.messages.get(chat_id, [])
                if (topic is None or m.topic_id == topic)
                and (before_id is None or m.id < before_id)
                and (until is None or m.date < until)
            ),
            key=lambda m: m.id,
            reverse=True,
        )
        out: list[Message] = []
        for seen, m in enumerate(stream, start=1):
            if since is not None and m.date < since:
                return History(self._seen(out), None)
            if self._plain(chat_id, m):
                out.append(m)
                if len(out) >= limit:
                    return History(self._seen(out), m.id)
            if seen >= limit * RAW_PER_MESSAGE:
                return History(self._seen(out), m.id, stopped_early=True)
        return History(self._seen(out), None)

    def _filter(
        self, chat_id: int, query: str, since: date | None, until: date | None
    ) -> list[Message]:
        return [
            m
            for m in self.messages.get(chat_id, [])
            if self._plain(chat_id, m)
            and _matches(m.text, query)
            and (since is None or m.date.date() >= since)
            and (until is None or m.date.date() <= until)
        ]

    def _sender_id(self, sender: str | None) -> int | None:
        if sender is None:
            return None
        users = [c for c in self.chats if c.kind == "user"]
        if not sender.startswith("@"):
            if int(sender) in self._seen_users | {c.id for c in users}:
                return int(sender)
            raise GatewayError(UNSEEN_USER)
        for chat in users:
            if (chat.username or "").lower() == sender[1:].lower():
                return chat.id
        raise GatewayError(UNSEEN_USER)

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
        return self._seen(found[:limit])

    async def search_global(
        self, query: str, *, since: date | None, until: date | None, limit: int
    ) -> list[Message]:
        self._enter("search_global")
        found = [m for chat_id in self.messages for m in self._filter(chat_id, query, since, until)]
        found.sort(key=lambda m: m.date, reverse=True)
        return self._seen(found[:limit])

    async def around(
        self, chat_id: int, message_id: int, *, before: int, after: int
    ) -> tuple[list[Message], Message, list[Message]]:
        self._enter("around")
        ordered = sorted(self.messages.get(chat_id, []), key=lambda m: m.id)
        target = next((m for m in ordered if m.id == message_id), None)
        if target is None or not self._plain(chat_id, target):
            raise GatewayError(f"No message {message_id} in that chat.")
        forum = any(c.id == chat_id and c.is_forum for c in self.chats)
        thread = [m for m in ordered if not forum or m.topic_id == target.topic_id]
        index = thread.index(target)
        # Like the live gateway: take `before`/`after` raw neighbours, then drop service ones.
        earlier = thread[max(0, index - before) : index] if before else []
        later = thread[index + 1 : index + 1 + after]
        earlier = [m for m in earlier if self._plain(chat_id, m)]
        later = [m for m in later if self._plain(chat_id, m)]
        self._seen([target])
        return self._seen(earlier), target, self._seen(later)

    async def close(self) -> None:
        self._enter("close")
