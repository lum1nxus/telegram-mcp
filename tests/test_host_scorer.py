import json
from collections import Counter
from typing import Any

import pytest

from tests.conftest import Call
from tgmcp.host.backends import ToolCall
from tgmcp.host.benchdata import QUESTIONS, SMOKE_IDS, Question, build_seed
from tgmcp.host.loop import RunResult, Step
from tgmcp.host.scorer import NOTHING_FOUND_WORDS, Score, score, table

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
        "ничего",
        "не найдено",
        "nic",
        "brak",
    )
    q = question(must_say_nothing_found=True, tool_sequence=[])
    searched = step("search_messages")
    assert score(q, run("Nie znalazłem nic.", searched), TITLES, IDS).answer_ok is True
    assert score(q, run("A technical answer.", searched), TITLES, IDS).answer_ok is False


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
    assert lines[-1] == "no_invention checks ids and exact chat titles only"


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
            assert "Private" in q.answer_must_not_contain


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
    assert all(q.answer_must_contain == ["read"] and q.answer_must_not_contain for q in refusal)


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_messages", {"chat": "Hiking Club"}, '"next_before_id":71'),
        ("get_messages", {"chat": "-1001", "before_id": 71}, "Ridge trail closed"),
        ("get_messages", {"chat": "-1008", "limit": 5}, '"next_before_id":6'),
        ("list_topics", {"chat": "Expats Hub"}, '{"id":7,"title":"Cars"'),
        ("search_messages", {"query": "ridge", "chat": "Hiking Club"}, '"id":60'),
        ("search_messages", {"query": "hydraulik"}, "wtorek"),
        ("search_messages", {"query": "лифт"}, "Kasia"),
        ("search_messages", {"query": "granite", "chat": "Hiking Club"}, '"scope":"Hiking Club"'),
        ("search_messages", {"query": "remont"}, "poniedziałku"),
        ("search_messages", {"query": "домофон"}, "50"),
        ("search_messages", {"query": "secret plan"}, '"hits":[]'),
        ("search_messages", {"query": "plan"}, '"hits":[]'),
        ("get_messages", {"chat": "-1007"}, "No chat matches '-1007'"),
        ("get_message_context", {"chat": "-1001", "message_id": 50}, "Granite peak"),
        ("list_chats", {"include_archived": True}, '"title":"Old"'),
        ("get_chat", {"chat": "Nora"}, '"username":"nora"'),
    ],
)
async def test_the_seed_holds_what_the_rules_expect(
    call: Call, name: str, arguments: dict[str, Any], expected: str
) -> None:
    result = await call(name, arguments)
    if result.is_error:
        text = result.content[0].text  # type: ignore[union-attr]
    else:
        text = json.dumps(result.structured_content, ensure_ascii=False, separators=(",", ":"))
    assert expected in text
