"""Rule-based scoring of a bench run (spec section 5.2): no model judges another.

`no_invention` checks chat ids, message ids the answer cites as ids (`id 5`, `#5`, `message 5`,
`(5)`) and the seed's chat titles (case-sensitive: they are proper nouns); dates and other names
are not checked (spec section 8). Its corpus is the question plus every tool result: what the
user typed is not an invention.
"""

import re
from dataclasses import dataclass
from typing import get_args

from tgmcp.host.benchdata import Category, Question, Term
from tgmcp.host.loop import RunResult, Step

NOTHING_FOUND_WORDS = (
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
LOOKING_TOOLS = frozenset({"search_messages", "get_messages", "scan_chat"})
FOOTER = (
    "no_invention checks chat ids, message ids cited as ids, and the seed's chat titles; "
    "dates and other names are not checked"
)
_CHAT_ID = re.compile(r"(?<![\w-])-\d{3,}(?!\d)")
# A number standing alone: not a part of a date (2026-10-21, 21.10.2026), a time (10:30), a
# fraction or a path. A full stop or a colon next to it counts only when a digit is on its other
# side (21.10, 10:30): "message 119." ends a sentence and `"id":119` is JSON.
_NUMBER_BEFORE = r"(?<![\w\-/])(?<!\d[.:])"
_NUMBER_AFTER = r"(?![\w\-/]|[.:]\d)"
# Digits the answer cites as a message id: right after `id`, `#`, `message(s)` or in brackets.
_CITED_ID = re.compile(
    r"(?:(?<!\w)id\s*[:=]?\s*|#\s*|(?<!\w)messages?\s+(?:id\s*[:=]?\s*)?)(\d+)"
    + _NUMBER_AFTER
    + r"|\((\d+)\)",
    re.IGNORECASE,
)
_COLUMNS = ("category", "n", "tools", "args", "answer", "no_inv", "steps", "s")
_WIDTHS = (14, 4, 6, 6, 7, 7, 6, 7)


@dataclass
class Score:
    tools_ok: bool
    args_ok: bool
    answer_ok: bool
    no_invention: bool
    steps: int
    cuts: int
    seconds: float


def _title(title: str) -> re.Pattern[str]:
    """A chat title as a whole word or phrase, case-sensitive: "private" is not "Private"."""
    return re.compile(rf"(?<!\w){re.escape(title)}(?!\w)")


def _number(digits: str) -> re.Pattern[str]:
    return re.compile(_NUMBER_BEFORE + re.escape(digits) + _NUMBER_AFTER)


def found(term: str, text: str) -> bool:
    """A `Question` answer term in `text`: a whole word or phrase, case-insensitive; a trailing
    `*` makes it a prefix, a leading `=` makes it case-sensitive. A term of digits only must
    stand alone, so "21" is not found in "2026-10-21", "21.10" or "10:21"."""
    if term.isdigit():
        return _number(term).search(text) is not None
    sensitive = term.startswith("=")
    stem = term.endswith("*")
    word = term.removeprefix("=").removesuffix("*")
    pattern = rf"(?<!\w){re.escape(word)}" + ("" if stem else r"(?!\w)")
    return re.search(pattern, text, 0 if sensitive else re.IGNORECASE) is not None


def _found_any(term: Term, text: str) -> bool:
    """A tuple of terms is met when any one of them is found."""
    return any(found(one, text) for one in ((term,) if isinstance(term, str) else term))


def _sequence(q: Question, steps: list[Step]) -> tuple[bool, bool]:
    """Each expected tool in order (a subsequence); predicates on the step each one matched."""
    position = 0
    args_ok = True
    for name, rules in q.tool_sequence:
        matched = next((i for i in range(position, len(steps)) if steps[i].call.name == name), None)
        if matched is None:
            return False, False
        arguments = steps[matched].call.arguments
        args_ok = args_ok and all(rule(arguments.get(arg)) for arg, rule in rules.items())
        position = matched + 1
    return True, args_ok


def _answer_ok(q: Question, answer: str | None) -> bool:
    if answer is None:
        return False
    ok = all(_found_any(term, answer) for term in q.answer_must_contain) and not any(
        found(term, answer) for term in q.answer_must_not_contain
    )
    if q.must_say_nothing_found:
        ok = ok and any(found(word, answer) for word in NOTHING_FOUND_WORDS)
    return ok


def _numbers(text: str, positive: set[int]) -> set[str]:
    ids = set(_CHAT_ID.findall(text))
    ids |= {str(n) for n in positive if re.search(rf"(?<![\w-]){n}(?!\d)", text)}
    return ids


def cited_ids(answer: str) -> set[str]:
    """Message ids the answer cites as ids; a bare number is never one."""
    return {first or second for first, second in _CITED_ID.findall(answer)}


def _no_invention(answer: str, corpus: str, seed_titles: set[str], seed_ids: set[int]) -> bool:
    positive = {n for n in seed_ids if n > 0}
    if not _numbers(answer, positive) <= _numbers(corpus, positive):
        return False
    if any(_number(digits).search(corpus) is None for digits in cited_ids(answer)):
        return False
    return all(
        _title(title).search(corpus) is not None
        for title in seed_titles
        if _title(title).search(answer) is not None
    )


def score(q: Question, run: RunResult, seed_titles: set[str], seed_ids: set[int]) -> Score:
    tools_ok, args_ok = _sequence(q, run.steps)
    if q.must_say_nothing_found:
        tools_ok = tools_ok and any(step.call.name in LOOKING_TOOLS for step in run.steps)
    corpus = "\n".join([q.text, *(step.result_text for step in run.steps)])
    return Score(
        tools_ok=tools_ok,
        args_ok=args_ok,
        answer_ok=_answer_ok(q, run.answer),
        no_invention=_no_invention(run.answer or "", corpus, seed_titles, seed_ids),
        steps=len(run.steps),
        cuts=sum(step.cut for step in run.steps),
        seconds=run.seconds,
    )


def _line(cells: tuple[str, ...]) -> str:
    first = cells[0].ljust(_WIDTHS[0])
    rest = "".join(cell.rjust(width) for cell, width in zip(cells[1:], _WIDTHS[1:], strict=True))
    return (first + rest).rstrip()


def _row(label: str, scores: list[Score]) -> str:
    n = len(scores)

    def percent(values: list[bool]) -> str:
        return f"{round(100 * sum(values) / n)}%"

    return _line(
        (
            label,
            str(n),
            percent([s.tools_ok for s in scores]),
            percent([s.args_ok for s in scores]),
            percent([s.answer_ok for s in scores]),
            percent([s.no_invention for s in scores]),
            f"{sum(s.steps for s in scores) / n:.1f}",
            f"{sum(s.seconds for s in scores) / n:.1f}",
        )
    )


def table(rows: list[tuple[Question, Score]], header: str) -> str:
    """Plain text: the header, column names, one line per category present, total, footer."""
    lines = [header, _line(_COLUMNS)]
    for category in get_args(Category):
        scores = [s for q, s in rows if q.category == category]
        if scores:
            lines.append(_row(category, scores))
    if rows:
        lines.append(_row("total", [s for _, s in rows]))
    lines.append(FOOTER)
    return "\n".join(lines)
