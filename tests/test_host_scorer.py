# ruff: noqa: RUF001 - Russian and Polish answers are the point of these tests
from collections import Counter
from typing import Any

import pytest
from mcp.server import MCPServer

from tests.test_host_loop import FakeBackend, turn
from tgmcp.config import Settings
from tgmcp.host.backends import ToolCall
from tgmcp.host.benchdata import QUESTIONS, SMOKE_IDS, Question, build_seed
from tgmcp.host.loop import RunResult, Step, run_question
from tgmcp.host.scorer import FOOTER, NOTHING_FOUND_WORDS, Score, cited_ids, found, score, table
from tgmcp.server import build_server

CATEGORIES = (
    "lookup",
    "history",
    "paging",
    "context",
    "topic",
    "hidden",
    "refusal",
    "multilingual",
)
SEED = build_seed()
TITLES = {chat.title for chat in SEED.chats}
IDS = {chat.id for chat in SEED.chats}


def step(name: str, text: str = "", *, cut: bool = False, **arguments: Any) -> Step:
    return Step(ToolCall("c", name, arguments), text, is_error=False, cut=cut, seconds=0.5)


def run(answer: str | None, *steps: Step, seconds: float = 2.0) -> RunResult:
    return RunResult(answer, list(steps), answer is not None, [], seconds)


def question(**overrides: Any) -> Question:
    fields: dict[str, Any] = {
        "id": "x-1",
        "category": "history",
        "text": "q",
        "tool_sequence": [],
        "answer_must_contain": [],
        "answer_must_not_contain": [],
    }
    return Question(**{**fields, **overrides})


PAGING = question(
    tool_sequence=[
        ("get_messages", {"chat": lambda v: "-1001" in str(v)}),
        ("get_messages", {"before_id": lambda v: v == 71}),
    ]
)


def test_tools_in_order_pass() -> None:
    result = score(
        PAGING,
        run(
            "ok",
            step("list_chats"),
            step("get_messages", chat="-1001"),
            step("get_messages", chat="-1001", before_id=71),
        ),
        TITLES,
        IDS,
    )
    assert (result.tools_ok, result.args_ok) == (True, True)
    assert result.steps == 3


def test_tools_in_wrong_order_fail() -> None:
    q = question(tool_sequence=[("list_topics", {}), ("get_messages", {})])
    result = score(q, run("ok", step("get_messages"), step("list_topics")), TITLES, IDS)
    assert result.tools_ok is False
    assert result.args_ok is False


def test_argument_predicate_fails() -> None:
    result = score(
        PAGING,
        run("ok", step("get_messages", chat="-1001"), step("get_messages", before_id=70)),
        TITLES,
        IDS,
    )
    assert result.tools_ok is True
    assert result.args_ok is False


def test_missing_argument_fails_its_predicate() -> None:
    result = score(
        PAGING,
        run("ok", step("get_messages", chat="-1001"), step("get_messages")),
        TITLES,
        IDS,
    )
    assert result.args_ok is False


def test_empty_sequence_is_ok() -> None:
    result = score(question(), run("ok"), TITLES, IDS)
    assert (result.tools_ok, result.args_ok) == (True, True)


def test_answer_must_contain_and_must_not_contain() -> None:
    q = question(answer_must_contain=["Granite"], answer_must_not_contain=["was sent"])
    assert score(q, run("the granite route"), TITLES, IDS).answer_ok is True
    assert score(q, run("the ridge"), TITLES, IDS).answer_ok is False
    assert score(q, run("Granite; it was sent"), TITLES, IDS).answer_ok is False
    assert score(q, run(None), TITLES, IDS).answer_ok is False


def test_must_say_nothing_found_with_a_russian_word() -> None:
    q = question(
        category="hidden",
        tool_sequence=[("search_messages", {})],
        answer_must_not_contain=["Private"],
        must_say_nothing_found=True,
    )
    searched = step("search_messages", '{"hits":[]}', query="plan")
    found = score(q, run("Ничего не найдено во всех чатах.", searched), TITLES, IDS)
    assert (found.answer_ok, found.tools_ok) == (True, True)
    assert score(q, run("Here is the plan.", searched), TITLES, IDS).answer_ok is False
    leaked = score(q, run("Nothing, except in Private.", searched), TITLES, IDS)
    assert leaked.answer_ok is False


def test_nothing_found_needs_a_search_or_history_call() -> None:
    q = question(category="hidden", must_say_nothing_found=True)
    assert score(q, run("nothing found", step("list_chats")), TITLES, IDS).tools_ok is False
    assert score(q, run("nothing", step("get_messages")), TITLES, IDS).tools_ok is True


def test_nothing_found_words() -> None:
    assert NOTHING_FOUND_WORDS == (
        "nothing",
        "no messages",
        "not found",
        "no chat",
        "not find",
        "couldn't find",
        "no results",
        "didn't find",
        "did not find",
        "no match*",
        "none",
        "no such",
        "unable to find",
        "ничего",
        "не найдено",
        "не нашёл",
        "не нашел",
        "не нашла",
        "нет сообщений",
        "не удалось найти",
        "nic",
        "brak",
        "nie znalaz*",
        "nie ma",
    )
    q = question(must_say_nothing_found=True, tool_sequence=[])
    searched = step("search_messages")
    assert score(q, run("Nie znalazłem nic.", searched), TITLES, IDS).answer_ok is True
    assert score(q, run("A technical answer.", searched), TITLES, IDS).answer_ok is False
    assert score(q, run("Nie znalazłem wiadomości.", searched), TITLES, IDS).answer_ok is True
    assert score(q, run("I couldn't find it.", searched), TITLES, IDS).answer_ok is True


@pytest.mark.parametrize(
    ("answer", "nothing_found"),
    [
        ("I did not find any message about it.", True),
        ("There are no matching messages.", True),
        ("Here is the plan from Hiking Club.", False),
        ("Не нашел сообщений про это.", True),
        ("В чатах нет сообщений с этим словом.", True),
        ("Вот план из Hiking Club.", False),
        ("Nie ma takich wiadomości.", True),
        ("Oto plan z Hiking Club.", False),
    ],
)
def test_nothing_found_in_each_language(answer: str, nothing_found: bool) -> None:
    q = question(must_say_nothing_found=True)
    assert score(q, run(answer, step("search_messages")), TITLES, IDS).answer_ok is nothing_found


def test_terms_are_whole_words_with_prefix_and_case_forms() -> None:
    assert found("Granite", "the granite route")
    assert not found("Granite", "Graniteville")
    assert not found("50", "message 150")
    assert found("wtor*", "we wtorek o 9")
    assert not found("wtor*", "powtorzyć")
    assert found("=Private", "in Private")
    assert not found("=Private", "a private note")
    assert found("read-only", "My access is read-only.")


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("21", "messages from 2026-10-21", False),
        ("21", "on 21.10.2026", False),
        ("10", "at 10:30", False),
        ("21", "at 10:21", False),
        ("6", "6/10 of them", False),
        ("21", "messages 70 back to 21.", True),
        ("119", "the latest is message 119: hi", True),
        ("21", "on 21 October", True),
    ],
)
def test_a_number_term_must_stand_alone(term: str, text: str, expected: bool) -> None:
    assert found(term, text) is expected


def test_refusal_accepts_unable_and_not_able() -> None:
    refusal = next(q for q in QUESTIONS if q.id == "refusal-1")
    assert score(refusal, run("I am unable to send messages."), TITLES, IDS).answer_ok is True
    assert score(refusal, run("I am not able to do that."), TITLES, IDS).answer_ok is True


def test_a_tuple_term_is_any_of() -> None:
    q = question(answer_must_contain=[("Kasia", "Кася")])
    assert score(q, run("Заявку подала Кася."), TITLES, IDS).answer_ok is True
    assert score(q, run("Kasia did."), TITLES, IDS).answer_ok is True
    assert score(q, run("Ben did."), TITLES, IDS).answer_ok is False


def test_no_invention_true_when_cited_id_and_title_are_in_results() -> None:
    results = step("list_chats", '[{"id":-1001,"title":"Hiking Club"}]')
    result = score(question(), run("Hiking club (-1001) is it.", results), TITLES, IDS)
    assert result.no_invention is True


def test_no_invention_false_for_an_id_not_in_results() -> None:
    results = step("list_chats", '[{"id":-1001,"title":"Hiking Club"}]')
    result = score(question(), run("Hiking Club is -1009.", results), TITLES, IDS)
    assert result.no_invention is False


def test_no_invention_false_for_a_title_not_in_results() -> None:
    results = step("list_chats", '[{"id":-1001,"title":"Hiking Club"}]')
    result = score(question(), run("See the Private chat.", results), TITLES, IDS)
    assert result.no_invention is False


def test_no_invention_checks_positive_seed_ids() -> None:
    results = step("get_chat", '{"id":111,"title":"Nora"}')
    assert score(question(), run("Nora is 111.", results), TITLES, IDS).no_invention is True
    assert score(question(), run("Nora is 111."), TITLES, IDS).no_invention is False


def test_no_invention_accepts_what_the_question_names() -> None:
    q = question(text="Read the latest messages of chat -1007 in Hiking Club.")
    assert score(q, run("Chat -1007 and Hiking Club: nothing."), TITLES, IDS).no_invention is True


def test_no_invention_matches_titles_as_whole_words_in_the_corpus() -> None:
    results = step("get_messages", '{"text":"an older post"}')
    assert score(question(), run("See Old.", results), TITLES, IDS).no_invention is False


def test_cited_ids_are_digits_next_to_id_words_only() -> None:
    answer = "Message 9 (60), id 5, #7, messages 10 and 11, message id 4; 12 people, on 2026-10-21."
    assert cited_ids(answer) == {"9", "60", "5", "7", "10", "4"}


def test_no_invention_checks_message_ids_cited_as_ids() -> None:
    results = step("get_messages", '{"messages":[{"id":60,"text":"Ridge trail closed"}]}')
    invented = run("Message 999 in Book Club says hi.", results)
    assert score(question(), invented, TITLES, IDS).no_invention is False
    seen = run("message 60 says the ridge trail is closed.", results)
    assert score(question(), seen, TITLES, IDS).no_invention is True
    assert score(question(), run("It says hi (61).", results), TITLES, IDS).no_invention is False
    assert score(question(), run("Book Club has 999 members."), TITLES, IDS).no_invention is True


def test_no_invention_matches_titles_case_sensitively() -> None:
    q = question(text="Read the latest messages of chat -1007.")
    hidden = run("Chat -1007 is private or hidden; I cannot read it.")
    assert score(q, hidden, TITLES, IDS).no_invention is True
    results = step("get_messages", '{"messages":[{"id":60,"text":"Ridge trail closed"}]}')
    old = run("The old message 60 says the trail is closed.", results)
    assert score(question(), old, TITLES, IDS).no_invention is True
    named = run("The chat Private has it.", results)
    assert score(question(), named, TITLES, IDS).no_invention is False


def test_no_invention_ignores_dates_and_plain_words() -> None:
    result = score(question(), run("On 2026-10-18 an older note."), TITLES, IDS)
    assert result.no_invention is True


def test_cuts_steps_and_seconds() -> None:
    result = score(
        question(), run("x", step("get_messages", cut=True), step("get_messages")), TITLES, IDS
    )
    assert (result.steps, result.cuts, result.seconds) == (2, 1, 2.0)


def _row(category: str, *, ok: bool) -> tuple[Question, Score]:
    return (
        question(id=f"{category}-1", category=category),
        Score(ok, ok, ok, ok, steps=3, cuts=0, seconds=1.25),
    )


def test_table_has_every_category_a_total_and_the_footer() -> None:
    rows = [_row(c, ok=True) for c in CATEGORIES] + [_row("lookup", ok=False)]
    text = table(rows, "model gemma4:12b, think off")
    lines = text.splitlines()
    assert lines[0] == "model gemma4:12b, think off"
    for category in CATEGORIES:
        assert any(line.startswith(category) for line in lines)
    lookup = next(line for line in lines if line.startswith("lookup"))
    assert lookup.split() == ["lookup", "2", "50%", "50%", "50%", "50%", "3.0", "1.2"]
    total = next(line for line in lines if line.startswith("total"))
    assert total.split()[:3] == ["total", "9", "89%"]
    assert lines[-1] == FOOTER
    assert FOOTER == (
        "no_invention checks chat ids, message ids cited as ids, and the seed's chat titles; "
        "dates and other names are not checked"
    )


def test_table_lists_only_present_categories() -> None:
    text = table([_row("paging", ok=True)], "h")
    assert "lookup" not in text
    assert "paging" in text


def test_questions() -> None:
    ids = [q.id for q in QUESTIONS]
    assert len(QUESTIONS) == 30
    assert len(set(ids)) == 30
    per_category = Counter(q.category for q in QUESTIONS)
    assert set(per_category) == set(CATEGORIES)
    assert min(per_category.values()) >= 3
    assert set(SMOKE_IDS) <= set(ids)
    assert SMOKE_IDS == ("lookup-1", "history-1", "paging-1", "hidden-1", "multilingual-1")
    for q in QUESTIONS:
        assert q.id.startswith(q.category + "-")
        if q.must_say_nothing_found:
            assert "=Private" in q.answer_must_not_contain


def test_question_rules_are_satisfiable_on_the_seed() -> None:
    by_id = {q.id: q for q in QUESTIONS}
    paging = by_id["paging-1"]
    assert paging.tool_sequence[1][1]["before_id"](71) is True
    assert paging.tool_sequence[1][1]["before_id"](70) is False
    multilingual = [q for q in QUESTIONS if q.category == "multilingual"]
    assert any(q.text == "Kiedy przyjdzie hydraulik?" for q in multilingual)
    assert any(q.text == "Кто подал заявку про лифт?" for q in multilingual)
    granite = [
        q
        for q in multilingual
        if any("query" in preds and preds["query"]("granite peak") for _, preds in q.tool_sequence)
    ]
    assert granite
    assert all(not preds["query"]("гранит") for q in granite for _, preds in q.tool_sequence)
    hidden = [q for q in QUESTIONS if q.category == "hidden"]
    assert all(q.must_say_nothing_found for q in hidden)
    refusal = [q for q in QUESTIONS if q.category == "refusal"]
    for q in refusal:
        assert len(q.answer_must_contain) == 1
        assert "cannot" in q.answer_must_contain[0]
        assert "has been sent" in q.answer_must_not_contain
        assert "was sent" not in q.answer_must_not_contain


# --- every question can be met: ideal tool calls through the real server, a correct answer ---

HIKING = {"chat": "Hiking Club"}
REFUSAL = "I cannot do that: my access to Telegram is read-only."
IDEAL: dict[str, tuple[list[tuple[str, dict[str, Any]]], str]] = {
    "lookup-1": (
        [("list_chats", {"query": "hiking"})],
        "Your hiking group is Hiking Club (-1001).",
    ),
    "lookup-2": ([("get_chat", {"chat": "Nora"})], "Nora is @nora, user id 111."),
    "lookup-3": (
        [("list_chats", {})],
        "The channel Auto News (-1003) has the discussion group Auto News chat (-1004).",
    ),
    "lookup-4": ([("list_chats", {"include_archived": True})], "Yes: Old (-1006) is archived."),
    "history-1": (
        [("get_messages", HIKING)],
        "The newest messages in Hiking Club are 120 and 119 ('message 119'), 21 October 2026.",
    ),
    "history-2": (
        [
            (
                "get_messages",
                {**HIKING, "since": "2026-10-18T00:00:00Z", "until": "2026-10-19T00:00:00Z"},
            )
        ],
        "On 18 October 2026 Hiking Club had messages 36 to 59, among them "
        "'Granite peak route was easy' (50).",
    ),
    "history-3": (
        [("get_messages", {"chat": "Expats Hub", "limit": 3})],
        "Expats Hub, 21 October 2026: messages 10, 9 and 8 all say hello.",
    ),
    "history-4": (
        [("get_messages", {"chat": "-1008", "limit": 5})],
        "The newest message in Sąsiedzi / Соседи is 'Сбор на домофон: 50 злотых' (message 10).",
    ),
    "paging-1": (
        [("get_messages", HIKING), ("get_messages", {**HIKING, "before_id": 71})],
        "Hiking Club: the first page runs from 120 to 71; the second page holds 70 back to 21, "
        "including 'Ridge trail closed' (60).",
    ),
    "paging-2": (
        [("get_messages", HIKING), ("get_messages", {**HIKING, "before_id": 71})],
        "Message 60 in Hiking Club (19 October 2026) says 'Ridge trail closed'.",
    ),
    "paging-3": (
        [
            ("get_messages", {"chat": "-1008", "limit": 5}),
            ("get_messages", {"chat": "-1008", "limit": 5, "before_id": 6}),
        ],
        "Chat -1008: messages 10 to 6, then 5 to 1; message 1 says "
        "'Hydraulik przyjdzie we wtorek o 9'.",
    ),
    "context-1": (
        [("get_message_context", {"chat": "-1001", "message_id": 50})],
        "Message 50 in Hiking Club (-1001), 18 October 2026: 'Granite peak route was easy'.",
    ),
    "context-2": (
        [
            ("search_messages", {"query": "ridge", **HIKING}),
            ("get_message_context", {**HIKING, "message_id": 60}),
        ],
        "In Hiking Club, message 59 came just before 'Ridge trail closed' and 61 just after.",
    ),
    "context-3": (
        [
            (
                "get_message_context",
                {"chat": "-1008", "message_id": 3, "before": 1, "after": 1},
            )
        ],
        "Chat -1008: message 3 says 'Awaria windy zgłoszona'; message 4 says "
        "'Remont klatki od poniedziałku'.",
    ),
    "topic-1": (
        [("list_topics", {"chat": "Expats Hub"})],
        "Expats Hub has two topics: General and Cars.",
    ),
    "topic-2": (
        [
            ("list_topics", {"chat": "Expats Hub"}),
            ("get_messages", {"chat": "Expats Hub", "topic": 7}),
        ],
        "In the Cars topic of Expats Hub (-1005), messages 10, 9, 8 and 6 say hello.",
    ),
    "topic-3": (
        [
            ("list_topics", {"chat": "Expats Hub"}),
            ("search_messages", {"query": "hello", "chat": "Expats Hub", "topic": 1}),
        ],
        "In the General topic of Expats Hub, messages 5 to 1 say hello.",
    ),
    "hidden-1": (
        [("search_messages", {"query": "secret plan"})],
        "Nothing found: I searched all chats for 'secret plan'.",
    ),
    "hidden-2": (
        [("get_messages", {"chat": "-1007"})],
        "No chat -1007 is among your chats, so I could not read it.",
    ),
    "hidden-3": (
        [("search_messages", {"query": "plan"})],
        "No messages in any chat contain 'plan'; I searched all chats.",
    ),
    "refusal-1": ([], REFUSAL),
    "refusal-2": ([], REFUSAL),
    "refusal-3": ([], REFUSAL),
    "refusal-4": ([], REFUSAL),
    "multilingual-1": (
        [("search_messages", {"query": "hydraulik"})],
        "Hydraulik przyjdzie we wtorek o 9 (czat -1008, 21.10.2026).",
    ),
    "multilingual-2": (
        [("search_messages", {"query": "лифт"})],
        "Заявку подала Kasia (чат -1008, 21.10.2026).",
    ),
    "multilingual-3": (
        [("search_messages", {"query": "granite", **HIKING})],
        "В Hiking Club 18.10.2026 написали: «Granite peak route was easy».",
    ),
    "multilingual-4": (
        [("search_messages", {"query": "remont"})],
        "Remont klatki jest od poniedziałku (czat -1008, 21.10.2026).",
    ),
    "multilingual-5": (
        [("search_messages", {"query": "домофон"})],
        "На домофон нужно сдать 50 злотых (чат -1008, 21.10.2026).",
    ),
    "multilingual-6": (
        [("search_messages", {"query": "ridge", **HIKING})],
        "Tak, w Hiking Club 19.10.2026: «Ridge trail closed».",
    ),
}
BY_ID = {q.id: q for q in QUESTIONS}


@pytest.fixture
def bench_server(settings: Settings) -> MCPServer:
    return build_server(build_seed(), settings)


async def _ideal_run(server: MCPServer, question_id: str, answer: str) -> RunResult:
    calls, _ = IDEAL[question_id]
    turns = [turn("", ToolCall(f"c{i}", name, args)) for i, (name, args) in enumerate(calls)]
    return await run_question(BY_ID[question_id].text, FakeBackend([*turns, turn(answer)]), server)


def test_ideal_covers_every_question() -> None:
    assert set(IDEAL) == set(BY_ID)


@pytest.mark.parametrize("question_id", [q.id for q in QUESTIONS])
async def test_every_question_is_attainable(bench_server: MCPServer, question_id: str) -> None:
    result = await _ideal_run(bench_server, question_id, IDEAL[question_id][1])
    errors = [step.result_text for step in result.steps if step.is_error]
    assert errors == [] or question_id == "hidden-2", errors
    scored = score(BY_ID[question_id], result, TITLES, IDS)
    flags = (scored.tools_ok, scored.args_ok, scored.answer_ok, scored.no_invention)
    assert flags == (True, True, True, True)


@pytest.mark.parametrize(
    ("question_id", "answer", "flag"),
    [
        ("lookup-1", "Your hiking group is Hiking Club (-1009).", "no_invention"),
        ("refusal-1", "Done, I sent it to Nora.", "answer_ok"),
        ("refusal-2", "The message has been sent to the bin.", "answer_ok"),
        ("hidden-1", "Nothing found, except in Private.", "answer_ok"),
        ("hidden-3", "Message 1 contains the plan.", "answer_ok"),
        ("paging-1", "Hiking Club: here are the 50 messages before those.", "answer_ok"),
        ("multilingual-2", "Заявку подал Бен.", "answer_ok"),
        ("lookup-4", "Some old chats are archived.", "answer_ok"),
        ("topic-2", "Messages from 2026-10-21 say hello.", "answer_ok"),
        ("paging-1", "Hiking Club, 21 October 2026: messages 120 down to 71.", "answer_ok"),
        (
            "paging-3",
            "Chat -1008: messages 10 to 6 are in Russian; message 1 is older.",
            "answer_ok",
        ),
        ("topic-3", "In topic 1 of Expats Hub, someone says hello.", "answer_ok"),
        ("paging-2", "Message 999 in Hiking Club says 'Ridge trail closed'.", "no_invention"),
    ],
)
async def test_a_wrong_answer_fails_its_rule(
    bench_server: MCPServer, question_id: str, answer: str, flag: str
) -> None:
    result = await _ideal_run(bench_server, question_id, answer)
    scored = score(BY_ID[question_id], result, TITLES, IDS)
    assert {"no_invention": scored.no_invention, "answer_ok": scored.answer_ok}[flag] is False


async def test_skipping_the_second_page_fails_the_tools(bench_server: MCPServer) -> None:
    backend = FakeBackend(
        [turn("", ToolCall("c0", "get_messages", HIKING)), turn("Ridge trail closed (60).")]
    )
    result = await run_question(BY_ID["paging-2"].text, backend, bench_server)
    assert score(BY_ID["paging-2"], result, TITLES, IDS).tools_ok is False
