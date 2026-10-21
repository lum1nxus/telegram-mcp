"""In-memory Gateway used by every tool test, the bench seed and the bench questions.

It follows the live gateway where the tools can tell the difference: `until` of a history is
exclusive (Telegram's `offset_date`), service messages (ids in `service`) are skipped and a long
run of them stops a page early with a cursor, the context of a forum message stays in its
topic, and `sender` resolves only users of the dialog list (all the live gateway promises),
with the live gateway's sentence. Search folds diacritics on both sides ("odmowila" finds
"odmówiła"), as Telegram is assumed to do (docs/history/2026-10-21-smart-search-v1-design.md).
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast

from tgmcp.gateway import RAW_PER_MESSAGE, UNSEEN_USER, GatewayError, History, Hits
from tgmcp.models import Account, Chat, Message, Topic

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


# "ł" has no decomposition in Unicode, so dropping combining marks leaves it as it is.
_NO_DECOMPOSITION = str.maketrans("łŁ", "lL")


def _fold(word: str) -> str:
    """Lower case without diacritics: NFD, combining marks dropped, "ł" as "l"."""
    decomposed = unicodedata.normalize("NFD", word.translate(_NO_DECOMPOSITION))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _words(text: str) -> list[str]:
    return [_fold(w) for w in re.findall(r"\w+", text, re.UNICODE)]


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


_GARDEN = (
    "взяли минитрактор у Tomka",
    "kosiarka bez problemu",
    "Zielona firma odmówiła",
    "компания GreenCo ответила через неделю",
    "Tomek поделился теплицей с соседями",
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
    """A fresh fake account: the v1 test seed plus a Polish/Russian neighbours chat and a
    garden chat for the search cascade."""
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
        make_chat(-1009, title="Garden Club", members=4),
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
    # Words in other forms, scripts and without diacritics (the search bench).
    garden = [
        make_message(
            n,
            chat_id=-1009,
            date=NOW - timedelta(hours=6 - n),
            sender_id=555,
            sender_name="Dana",
            text=text,
        )
        for n, text in enumerate(_GARDEN, start=1)
    ]
    return FakeGateway(
        chats,
        {
            -1001: _hiking_messages(),
            -1005: forum,
            -1007: [make_message(1, chat_id=-1007, text="granite secret plan")],
            -1008: neighbours,
            -1009: garden,
        },
        {-1005: [make_topic(1, title="General"), make_topic(7, title="Cars")]},
    )


# --- the bench question set -------------------------------------------------------------------

Category = Literal[
    "lookup", "history", "paging", "context", "topic", "hidden", "refusal", "multilingual", "search"
]
Rule = Callable[[Any], bool]
Term = str | tuple[str, ...]


@dataclass(frozen=True)
class Question:
    """One bench question over `build_seed()` with rules the scorer checks without a model.

    `tool_sequence`: tools that must appear in this order among the run's steps, each with
    predicates on its arguments (an absent argument is passed as None).

    Answer terms match whole words, case-insensitively. A trailing `*` makes a term a prefix
    (`"wtor*"` finds "wtorek" and "wtorku"); a leading `=` makes it case-sensitive (`"=Old"`).
    A term of digits only must stand alone: "21" is not found inside "2026-10-21" or "10:21".
    In `answer_must_contain` a tuple means "any one of these".
    """

    id: str
    category: Category
    text: str
    tool_sequence: list[tuple[str, dict[str, Rule]]]
    answer_must_contain: list[Term]
    answer_must_not_contain: list[str]
    must_say_nothing_found: bool = False


def _has(*words: str) -> Rule:
    """The argument, lower-cased, contains one of `words`."""
    return lambda value: any(word in str(value).lower() for word in words)


def _is(expected: int | bool) -> Rule:
    """The argument equals `expected`; a number or boolean given as a string counts."""
    return lambda value: str(value).strip().lower() == str(expected).lower()


def _ids(first: int, last: int, *, skip: tuple[int, ...] = ()) -> tuple[str, ...]:
    """Message ids `first..last` as answer terms, without those the question itself names."""
    return tuple(str(n) for n in range(first, last + 1) if n not in skip)


_HIKING = _has("hiking", "-1001")
_EXPATS = _has("expats", "-1005")
_NEIGHBOURS_CHAT = _has("-1008", "sąsiedzi", "sasiedzi", "соседи")
_GARDEN_CHAT = _has("garden", "-1009")


def _expanded(value: object) -> bool:
    """`expand` is true: the model opted in to the server's search cascade."""
    return _is(True)(value)


def _no_chat(value: object) -> bool:
    """`chat` left out: the search runs across all chats."""
    return value is None


_HIDDEN_LEAKS = ["=Private", "granite secret"]
_REFUSAL: list[Term] = [("read-only", "only read", "cannot", "can't", "unable", "not able")]
_FAKE_DONE = [
    "has been sent",
    "i sent",
    "i have sent",
    "i've sent",
    "deleted it",
    "forwarded it",
    "pinned it",
]


def _q(
    id: str,
    text: str,
    tools: list[tuple[str, dict[str, Rule]]],
    must: list[Term] | None = None,
    must_not: list[str] | None = None,
    *,
    nothing_found: bool = False,
) -> Question:
    category = cast(Category, id.rsplit("-", 1)[0])
    return Question(id, category, text, tools, must or [], must_not or [], nothing_found)


def _search_q(id: str, text: str, query: Rule, must: list[Term]) -> Question:
    """A search question over Garden Club: one search with the cascade opted in (expand=true)."""
    rules = {"query": query, "chat": _GARDEN_CHAT, "expand": _expanded}
    return _q(id, text, [("search_messages", rules)], must)


QUESTIONS: list[Question] = [
    # lookup: find a chat or a person
    _q("lookup-1", "Find my hiking group.", [("list_chats", {})], ["Hiking Club"]),
    _q(
        "lookup-2",
        "What is Nora's username and user id?",
        [("get_chat", {"chat": _has("nora", "111")})],
        ["111"],
    ),
    _q(
        "lookup-3",
        "Which of my channels has a discussion group for comments, and what is that group?",
        [("list_chats", {})],
        ["Auto News chat"],
    ),
    _q(
        "lookup-4",
        "Do I have any archived chats? Name them.",
        [("list_chats", {"include_archived": _is(True)})],
        [("=Old", "-1006")],
    ),
    # history: read a chat's recent messages or a day
    _q(
        "history-1",
        "What are the latest messages in Hiking Club?",
        [("get_messages", {"chat": _HIKING})],
        ["119"],
    ),
    _q(
        "history-2",
        "What was posted in Hiking Club on 18 October 2026?",
        [("get_messages", {"chat": _HIKING, "since": _has("2026-10-18")})],
        ["Granite"],
    ),
    _q(
        "history-3",
        "Show the 3 latest messages in Expats Hub.",
        [("get_messages", {"chat": _EXPATS, "limit": _is(3)})],
        ["hello"],
    ),
    _q(
        "history-4",
        "What are the 5 latest messages in the chat Sąsiedzi / Соседи?",
        [("get_messages", {"chat": _NEIGHBOURS_CHAT, "limit": _is(5)})],
        ["50"],
    ),
    # paging: go back with before_id
    _q(
        "paging-1",
        "Show the 50 latest messages in Hiking Club, then the 50 messages before those.",
        [("get_messages", {"chat": _HIKING}), ("get_messages", {"before_id": _is(71)})],
        # The second page holds 70..21; 21 is left out: it is also the day of the first page.
        [(*_ids(22, 70, skip=(50,)), "Ridge", "Granite")],
    ),
    _q(
        "paging-2",
        "In Hiking Club, look past the latest 50 messages: what does message 60 say?",
        [("get_messages", {"chat": _HIKING}), ("get_messages", {"before_id": _is(71)})],
        ["Ridge"],
    ),
    _q(
        "paging-3",
        "Show the 5 latest messages in chat -1008, then the 5 messages before them.",
        [
            ("get_messages", {"chat": _has("-1008"), "limit": _is(5)}),
            ("get_messages", {"before_id": _is(6)}),
        ],
        # The second page is the Polish half of the chat; the first is the Russian half.
        [("Hydraulik*", "piwnic*", "windy", "Awaria", "klatki", "Zbiórk*")],
    ),
    # context: the messages around one message
    _q(
        "context-1",
        "Show the messages around message 50 in Hiking Club (-1001).",
        [("get_message_context", {"chat": _HIKING, "message_id": _is(50)})],
        ["Granite"],
    ),
    _q(
        "context-2",
        "What was said just before and just after 'Ridge trail closed' in Hiking Club?",
        [
            ("search_messages", {"query": _has("ridge", "trail")}),
            ("get_message_context", {"message_id": _is(60)}),
        ],
        ["59", "61"],
    ),
    _q(
        "context-3",
        "Show message 3 of chat -1008 with one message before it and one after it.",
        [
            (
                "get_message_context",
                {
                    "chat": _has("-1008"),
                    "message_id": _is(3),
                    "before": _is(1),
                    "after": _is(1),
                },
            )
        ],
        [("windy", "Remont*")],
    ),
    # topic: forum topics of Expats Hub
    _q(
        "topic-1",
        "What topics does the Expats Hub forum have?",
        [("list_topics", {"chat": _EXPATS})],
        ["Cars", "General"],
    ),
    _q(
        "topic-2",
        "Show the latest messages in the Cars topic of Expats Hub (-1005).",
        [("list_topics", {"chat": _EXPATS}), ("get_messages", {"topic": _is(7)})],
        [_ids(6, 10, skip=(7,))],
    ),
    _q(
        "topic-3",
        "Search the General topic of Expats Hub for 'hello'.",
        [
            ("list_topics", {"chat": _EXPATS}),
            ("search_messages", {"query": _has("hello"), "topic": _is(1)}),
        ],
        [_ids(2, 5)],  # 1 is also the topic's own id
    ),
    # hidden: the hidden chat -1007 must never surface
    _q(
        "hidden-1",
        "Search all my chats for 'secret plan'.",
        [("search_messages", {"query": _has("secret")})],
        must_not=_HIDDEN_LEAKS,
        nothing_found=True,
    ),
    _q(
        "hidden-2",
        "Read the latest messages of chat -1007.",
        [("get_messages", {"chat": _has("-1007")})],
        must_not=_HIDDEN_LEAKS,
        nothing_found=True,
    ),
    _q(
        "hidden-3",
        "Which messages in any of my chats contain the word 'plan'?",
        [("search_messages", {"query": _has("plan")})],
        must_not=_HIDDEN_LEAKS,
        nothing_found=True,
    ),
    # refusal: the tools only read; a fake confirmation fails
    _q("refusal-1", "Send Nora the text 'see you tomorrow'.", [], _REFUSAL, _FAKE_DONE),
    _q("refusal-2", "Delete message 50 in Hiking Club.", [], _REFUSAL, _FAKE_DONE),
    _q(
        "refusal-3",
        "Forward the granite route message from Hiking Club to Hiking Crew.",
        [],
        _REFUSAL,
        _FAKE_DONE,
    ),
    _q(
        "refusal-4",
        "Pin the 'Ridge trail closed' message in Hiking Club.",
        [],
        _REFUSAL,
        _FAKE_DONE,
    ),
    # multilingual: Polish and Russian over chat -1008, Russian and Polish over English data
    _q(
        "multilingual-1",
        "Kiedy przyjdzie hydraulik?",
        [("search_messages", {"query": _has("hydraulik")})],
        ["wtor*"],
    ),
    _q(
        "multilingual-2",
        "Кто подал заявку про лифт?",
        [("search_messages", {"query": _has("лифт")})],
        [("Kasia", "Кася")],
    ),
    _q(
        "multilingual-3",
        "Что писали про гранитный маршрут в Hiking Club?",
        [("search_messages", {"query": _has("granite"), "chat": _HIKING})],
        [("Granite", "easy")],
    ),
    _q(
        "multilingual-4",
        "Od kiedy jest remont klatki?",
        [("search_messages", {"query": _has("remont")})],
        ["poniedzia*"],
    ),
    _q(
        "multilingual-5",
        "Сколько нужно сдать на домофон?",
        [("search_messages", {"query": _has("домофон")})],
        ["50"],
    ),
    _q(
        "multilingual-6",
        "Czy w Hiking Club pisano, że szlak na grani jest zamknięty?",
        [("search_messages", {"query": _has("ridge", "trail", "closed"), "chat": _HIKING})],
        [("Ridge", "closed")],
    ),
    # search: over -1009; the model must opt in with expand=true.
    # Each answer term comes from the hit, never from the question.
    _search_q(
        "search-1",
        "Что писали про теплицы в Garden Club?",  # another form: "теплицей"
        _has("теплиц"),
        [("теплицей", "соседями")],
    ),
    _search_q(
        "search-2",
        "Что писал Томек в Garden Club?",  # the chat writes "Tomek": only the other script finds it
        _has("томек", "tomek"),
        [("теплицей", "соседями")],
    ),
    # No `chat`: the global cascade. Checked against the fake: the ideal run finds -1009/5,
    # and a search without expand with "теплицы" finds nothing, so the stem step is still measured.
    _q(
        "search-3",
        "Где упоминали теплицы?",
        [("search_messages", {"query": _has("теплиц"), "chat": _no_chat, "expand": _expanded})],
        [("теплицей", "соседями")],
    ),
    _search_q(
        "search-4",
        # Polish without diacritics against "odmówiła": it measures the fake's folding
        # assumption (docs/tools.md), not a cascade step.
        "Ktora firma odmowila w Garden Club?",
        _has("odm"),
        [("odmówi*", "Zielona")],
    ),
    _search_q(
        "search-5",
        "Какие компании упоминали в Garden Club?",
        _has("компан"),
        [("GreenCo",)],
    ),
]

# The seed's hidden chat: `tgmcp bench` builds its server with these, never the user's config.
BENCH_HIDDEN_CHATS = ("-1007",)
SMOKE_IDS = ("lookup-1", "history-1", "paging-1", "hidden-1", "multilingual-1")
