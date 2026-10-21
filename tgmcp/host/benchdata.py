"""In-memory Gateway used by every tool test.

It follows the live gateway where the tools can tell the difference: `until` of a history is
exclusive (Telegram's `offset_date`), service messages (ids in `service`) are skipped and a long
run of them stops a page early with a cursor, the context of a forum message stays in its
topic, and `sender` resolves only users of the dialog list (all the live gateway promises),
with the live gateway's sentence.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tgmcp.gateway import RAW_PER_MESSAGE, UNSEEN_USER, GatewayError, History, Hits
from tgmcp.models import Account, Chat, Message, Topic

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"\w+", text, re.UNICODE)]


def _matches(text: str, query: str) -> bool:
    words = _words(text)
    return all(any(w.startswith(q) for w in words) for q in _words(query))


def make_message(id: int, **overrides: Any) -> Message:
    fields: dict[str, Any] = {
        "id": id,
        "chat_id": -1001,
        "topic_id": None,
        "date": NOW,
        "sender_id": 222,
        "sender_name": "Sam",
        "text": "hello",
    }
    return Message(**{**fields, **overrides})


def make_chat(id: int, **overrides: Any) -> Chat:
    fields: dict[str, Any] = {"id": id, "title": "Chat", "kind": "group"}
    return Chat(**{**fields, **overrides})


def make_topic(id: int, **overrides: Any) -> Topic:
    fields: dict[str, Any] = {"id": id, "title": "Topic"}
    return Topic(**{**fields, **overrides})


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

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error

    def _plain(self, chat_id: int, m: Message) -> bool:
        return m.id not in self.service.get(chat_id, set())

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
                return History(out, None)
            if self._plain(chat_id, m):
                out.append(m)
                if len(out) >= limit:
                    return History(out, m.id)
            if seen >= limit * RAW_PER_MESSAGE:
                return History(out, m.id, stopped_early=True)
        return History(out, None)

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

    def user_id_for(self, sender: str | None) -> int | None:
        if sender is None:
            return None
        users = [c for c in self.chats if c.kind == "user"]
        if not sender.startswith("@"):
            if int(sender) in {c.id for c in users}:
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
        sender_id = self.user_id_for(sender)
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
    ) -> Hits:
        self._enter("search_global")
        found = [m for chat_id in self.messages for m in self._filter(chat_id, query, since, until)]
        found.sort(key=lambda m: m.date, reverse=True)
        return Hits(found[:limit])

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
        return earlier, target, later

    async def close(self) -> None:
        self._enter("close")


_NEIGHBOURS = (
    "Hydraulik przyjdzie we wtorek o 9",
    "Ktoś ma klucz do piwnicy?",
    "Awaria windy zgłoszona",
    "Remont klatki od poniedziałku",
    "Zbiórka na domofon: 50 zł",
    "Сантехник придёт во вторник в 9",
    "У кого ключ от подвала?",
    "Лифт сломался, заявка подана",
    "Ремонт подъезда с понедельника",
    "Сбор на домофон: 50 злотых",
)


def _hiking_messages() -> list[Message]:
    out: list[Message] = []
    for n in range(1, 121):
        fields: dict[str, object] = {
            "chat_id": -1001,
            "date": NOW - timedelta(hours=120 - n),
            "text": f"message {n}",
        }
        if n % 10:
            fields.update(sender_id=333, sender_name="Ben")
        if n == 50:
            fields["text"] = "Granite peak route was easy"
        if n == 60:
            fields["text"] = "Ridge trail closed"
        if n == 120:
            fields["text"] = "x" * 4096
        out.append(make_message(n, **fields))
    return out


def build_seed() -> FakeGateway:
    """A fresh fake account: the v1 test seed plus a Polish/Russian neighbours chat."""
    chats = [
        make_chat(111, title="Nora", kind="user", username="nora"),
        make_chat(-1001, title="Hiking Club", members=3),
        make_chat(-1002, title="Hiking Crew"),
        make_chat(
            -1003,
            title="Auto News",
            kind="channel",
            username="autonews",
            discussion_chat_id=-1004,
        ),
        make_chat(-1004, title="Auto News chat"),
        make_chat(-1005, title="Expats Hub", is_forum=True),
        make_chat(-1006, title="Old", archived=True),
        make_chat(-1007, title="Private"),
        make_chat(-1008, title="Sąsiedzi / Соседи"),
    ]
    forum = [
        make_message(
            n, chat_id=-1005, topic_id=1 if n <= 5 else 7, date=NOW - timedelta(hours=10 - n)
        )
        for n in range(1, 11)
    ]
    neighbours = [
        make_message(
            n,
            chat_id=-1008,
            date=NOW - timedelta(hours=10 - n),
            sender_id=444,
            sender_name="Kasia",
            text=text,
        )
        for n, text in enumerate(_NEIGHBOURS, start=1)
    ]
    return FakeGateway(
        chats,
        {
            -1001: _hiking_messages(),
            -1005: forum,
            -1007: [make_message(1, chat_id=-1007, text="granite secret plan")],
            -1008: neighbours,
        },
        {-1005: [make_topic(1, title="General"), make_topic(7, title="Cars")]},
    )
