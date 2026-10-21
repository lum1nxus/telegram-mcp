"""Matching for the deep scan: folding, fragments, whole words, fuzzy words and safe regex.

Pure and deterministic: no I/O, no state. The scan builds one predicate per call with
`matcher` (the query is folded and the pattern compiled once) and applies it to each message.

- `fold` lower-cases and maps an explicit table of accented letters (Polish, ё→е, common Latin
  accents, ß→ss), so "Zażółć" and "zazolc" compare equal; every other letter is kept (й stays
  й, distinct from и).
- `fragment` is a folded substring ("трактор" finds "минитрактор").
- `words` needs every query word to be a whole word of the text, in any order.
- `tolerance` (ruling 3) compares each query word with whole text words: Levenshtein, with a
  trigram Jaccard gate in front for words of 7 letters and more. It is clamped by query-word
  length (ruling 6): up to 3 letters → 0, 4-6 letters → at most 1, 7 and more → as given. A
  transposition costs 2 edits, so with the clamp a swap of two letters in a 5-6-letter word is
  never a fuzzy match. In `fragment` mode with tolerance > 0 a query word matches a text word
  that it fuzzy-matches or is an exact substring of; a typo inside a compound is not caught.
- `regex` (ruling 4) searches the ORIGINAL text, case-insensitive, on at most 4 096
  characters; `compile_pattern` refuses patterns that could run forever (see its docstring).
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

MAX_PATTERN_CHARS = 200
MAX_TEXT_CHARS = 4096
MIN_JACCARD = 0.3
MIN_TRIGRAM_WORD = 2  # shorter words have no trigrams
SHORT_WORD = 3  # up to this many letters: no tolerance
MEDIUM_WORD = 6  # up to this many letters: tolerance at most 1
MAX_TOLERANCE = 2
MAX_UNBOUNDED = 3  # unbounded quantifiers a pattern may hold

_WORD = re.compile(r"\w+")
_BACK_REFERENCE = re.compile(r"\\[1-9]|\(\?P=")
_ESCAPE = re.compile(r"\\.", re.DOTALL)
_CHAR_CLASS = re.compile(r"\[\^?\]?[^\]]*\]")
_QUANTIFIER = re.compile(r"(?:[*+?]|\{(?:\d+,?\d*|,\d+)\})[?+]?")
_UNBOUNDED = re.compile(r"(?:[*+]|\{\d+,\})[?+]?")
_GROUP_PREFIX = re.compile(r"\?(?:P<\w+>|<[=!]|[=!:>]|[aiLmsux-]+:?)?")
_LEADING_ANY = re.compile(r"(\(\?[aiLmsux]+\))?\.\*\??(?![*+?{])")
_LOOKAROUND = ("?=", "?!", "?<=", "?<!")

_FOLD_TABLE = str.maketrans(
    {
        **dict.fromkeys("ą", "a"),
        **dict.fromkeys("ć", "c"),
        **dict.fromkeys("ę", "e"),
        **dict.fromkeys("ł", "l"),
        **dict.fromkeys("ń", "n"),
        **dict.fromkeys("ó", "o"),
        **dict.fromkeys("ś", "s"),
        **dict.fromkeys("źż", "z"),
        **dict.fromkeys("ё", "е"),
        **dict.fromkeys("àáâä", "a"),
        **dict.fromkeys("èéêë", "e"),
        **dict.fromkeys("ìíîï", "i"),
        **dict.fromkeys("òôö", "o"),
        **dict.fromkeys("ùúûü", "u"),
        **dict.fromkeys("ç", "c"),
        **dict.fromkeys("ñ", "n"),
        **dict.fromkeys("ß", "ss"),
    }
)
_FOREVER = "That pattern could run forever; simplify it."
_TOO_LONG = f"The pattern is longer than {MAX_PATTERN_CHARS} characters."


class PatternRefused(ValueError):  # noqa: N818 - name fixed by the interface
    """A regex pattern the scan will not run: too long, catastrophic or invalid."""


def fold(text: str) -> str:
    """Lower-case and map the accented letters of `_FOLD_TABLE`; nothing else changes."""
    return text.lower().translate(_FOLD_TABLE)


def words_of(text: str) -> list[str]:
    """The folded words of `text`."""
    return _WORD.findall(fold(text))


def trigrams(word: str) -> frozenset[str]:
    """Character trigrams of `word` padded as "  word "; empty for words under 2 letters."""
    if len(word) < MIN_TRIGRAM_WORD:
        return frozenset()
    padded = f"  {word} "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def levenshtein(a: str, b: str, max_distance: int) -> int:
    """Edit distance of `a` and `b`; `max_distance + 1` as soon as it must exceed the bound."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        if min(current) > max_distance:
            return max_distance + 1
        previous = current
    return min(previous[-1], max_distance + 1)


def clamp_tolerance(word: str, tolerance: int) -> int:
    """The tolerance a query word allows: 0 up to 3 letters, at most 1 up to 6, else as given."""
    if len(word) <= SHORT_WORD:
        return 0
    if len(word) <= MEDIUM_WORD:
        return min(1, tolerance)
    return tolerance


@dataclass(frozen=True)
class _QueryWord:
    """A folded query word prepared once: its clamped tolerance and (when gated) trigrams."""

    word: str
    tolerance: int
    grams: frozenset[str]


def _prepare(query_words: list[str], tolerance: int) -> list[_QueryWord]:
    prepared = []
    for word in query_words:
        tol = clamp_tolerance(word, tolerance)
        grams = trigrams(word) if tol and len(word) > MEDIUM_WORD else frozenset()
        prepared.append(_QueryWord(word, tol, grams))
    return prepared


def _fuzzy(query: _QueryWord, text_word: str) -> bool:
    if query.tolerance == 0:
        return query.word == text_word
    if abs(len(query.word) - len(text_word)) > query.tolerance:
        return False
    if query.grams:  # the gate filters only long words; short ones go straight to edits
        t_grams = trigrams(text_word)
        if t_grams and len(query.grams & t_grams) / len(query.grams | t_grams) < MIN_JACCARD:
            return False
    return levenshtein(query.word, text_word, query.tolerance) <= query.tolerance


def fuzzy_word(query_word: str, text_word: str, tolerance: int) -> bool:
    """Whether two folded words are within the clamped tolerance (gate on 7+ letters, edits)."""
    return _fuzzy(_prepare([query_word], tolerance)[0], text_word)


def _all_words_found(query: list[_QueryWord], text_words: set[str]) -> bool:
    if not query:
        return False
    return all(q.word in text_words or any(_fuzzy(q, t) for t in text_words) for q in query)


def _all_fragments_found(query: list[_QueryWord], text_words: set[str]) -> bool:
    if not query:
        return False
    return all(any(q.word in t or _fuzzy(q, t) for t in text_words) for q in query)


def fragment(query: str, text: str) -> bool:
    """Whether the folded query is a substring of the folded text; an empty query never is."""
    folded = fold(query)
    return bool(folded) and folded in fold(text)


def words(query: str, text: str, tolerance: int = 0) -> bool:
    """Whether every query word matches a whole text word (fuzzy when tolerance > 0)."""
    return _all_words_found(_prepare(words_of(query), tolerance), set(words_of(text)))


def fragment_fuzzy(query: str, text: str, tolerance: int) -> bool:
    """Whether every query word is a substring of a text word or fuzzy-matches one."""
    return _all_fragments_found(_prepare(words_of(query), tolerance), set(words_of(text)))


@dataclass
class _Group:
    """A parsed group (or the whole pattern): its items and whether it holds `|`."""

    items: list["_Item"] = field(default_factory=list)
    alternation: bool = False
    lookaround: bool = False


@dataclass
class _Item:
    """One atom or group with its quantifier ("" for none); a `|` is an unquantified item."""

    group: _Group | None
    quantifier: str


def _parse(simple: str, i: int = 0) -> tuple[_Group, int]:
    """Parse a blanked pattern from `i` up to the `)` that closes the current group."""
    group = _Group()
    while i < len(simple):
        c = simple[i]
        if c == ")":
            return group, i + 1
        child = None
        if c == "(":
            prefix = _GROUP_PREFIX.match(simple, i + 1)
            child, i = _parse(simple, prefix.end() if prefix else i + 1)
            child.lookaround = prefix is not None and prefix.group().startswith(_LOOKAROUND)
        else:
            group.alternation = group.alternation or c == "|"
            i += 1
        quantifier = _QUANTIFIER.match(simple, i) if c != "|" else None
        group.items.append(_Item(child, quantifier.group() if quantifier else ""))
        i = quantifier.end() if quantifier else i
    return group, i


def _quantifiers(group: _Group) -> Iterator[str]:
    for item in group.items:
        if item.quantifier:
            yield item.quantifier
        if item.group is not None:
            yield from _quantifiers(item.group)


def _has_alternation(group: _Group) -> bool:
    return group.alternation or any(
        item.group is not None and _has_alternation(item.group) for item in group.items
    )


def _is_unbounded(quantifier: str) -> bool:
    return _UNBOUNDED.fullmatch(quantifier) is not None


def _run(group: _Group) -> Iterator[str]:
    """The quantifiers of the group's sequence ("" for none), with plain groups flattened.

    An unquantified group without `|` that is not a lookaround is spliced into the sequence
    (an empty group vanishes); any other group counts as one atom with its quantifier.
    """
    for item in group.items:
        inner = item.group
        if (
            inner is not None
            and not item.quantifier
            and not inner.alternation
            and not inner.lookaround
        ):
            yield from _run(inner)
        else:
            yield item.quantifier


def _has_unbounded_in_a_row(group: _Group) -> bool:
    """Two unbounded atoms with nothing between them but optional (`?`) atoms."""
    previous_unbounded = False
    for quantifier in _run(group):
        if _is_unbounded(quantifier):
            if previous_unbounded:
                return True
            previous_unbounded = True
        elif not quantifier.startswith("?"):
            previous_unbounded = False
    return False


def _is_risky(group: _Group) -> bool:
    """A quantified group holding a quantifier or `|`, or two unbounded atoms in a row."""
    for item in group.items:
        inner = item.group
        if inner is not None and (
            _is_risky(inner)
            or (item.quantifier and (any(_quantifiers(inner)) or _has_alternation(inner)))
        ):
            return True
    return _has_unbounded_in_a_row(group)


def _blank(pattern: str) -> str:
    """The pattern with escapes and character classes replaced by a plain atom `x`."""
    return _CHAR_CLASS.sub("x", _ESCAPE.sub("x", pattern))


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a scan pattern with IGNORECASE, refusing what could run forever.

    The guard is a heuristic, not a proof. Escapes and character classes count as one plain
    atom, so `\\*`, `[*+]` and `\\(` are not taken for syntax. In this order:
    - a pattern longer than `MAX_PATTERN_CHARS` characters is refused;
    - back-references `\\1`...`\\9` and `(?P=name)` are refused;
    - any quantifier on a group that contains a quantifier, bounded counts and `?` included:
      `(a+)+`, `(a?){25}`, `(a{2,10}){2,10}`, `(.*a){20}`, `((ab)+)+` are refused;
    - any quantifier on a group that contains `|`, at any depth: `(a|aa)+`, `(a|ab){2,}` are
      refused (`cena|price` at the top level passes);
    - two unbounded atoms in a row (`*`, `+`, `{n,}`, lazy forms included) are refused:
      `a*a*`, `.*.*`, `\\w+\\w*`. Unquantified plain groups are seen through (`(.*)(.*)=`,
      `x*()x*=`) and an optional atom does not break the run (`a*b?a*c`, `x*(y)?x*=`);
    - one leading `.*` or lazy `.*?`, also right after an inline flag group at the very start,
      is stripped: under `search` it changes nothing, so `.*abc` and `.*?abc` compile as `abc`
      and `(?s).*abc` as `(?s)abc`; `.+abc` and `^.*abc` are kept as given and counted;
    - more than `MAX_UNBOUNDED` unbounded quantifiers after the strip are refused;
    - anything `re` cannot compile is refused.
    Knowingly refused although harmless: an optional group holding a quantifier (`(a+)?`)
    and a quantified group with an alternation nested deeper (`((a|b)c)+`).
    Knowingly not caught: unbounded runs separated by a literal (`a+.a+.a+c`, `.*a.*a.*c`;
    the count of unbounded quantifiers caps them), a run through an alternation group
    (`(a*|b)a*`), nested bounded repeats of plain atoms without a group
    (`a{1,30}b?a{1,30}`), overlapping alternation at the top level, and lookarounds, whose
    content is checked like any group but not for what it looks at.
    """
    if len(pattern) > MAX_PATTERN_CHARS:
        raise PatternRefused(_TOO_LONG)
    if _BACK_REFERENCE.search(pattern) or _is_risky(_parse(_blank(pattern))[0]):
        raise PatternRefused(_FOREVER)
    leading = _LEADING_ANY.match(pattern)
    stripped = (leading.group(1) or "") + pattern[leading.end() :] if leading else pattern
    unbounded = sum(_is_unbounded(q) for q in _quantifiers(_parse(_blank(stripped))[0]))
    if unbounded > MAX_UNBOUNDED:
        raise PatternRefused(_FOREVER)
    try:
        return re.compile(stripped, re.IGNORECASE)
    except re.error as e:
        raise PatternRefused(f"Not a valid pattern: {e}") from e


def regex(compiled: re.Pattern[str], text: str) -> bool:
    """Whether the pattern occurs in the first `MAX_TEXT_CHARS` characters of the original text."""
    return compiled.search(text[:MAX_TEXT_CHARS]) is not None


def matcher(mode: str, query: str, tolerance: int) -> Callable[[str], bool]:
    """The predicate for one scan: "fragment", "words" or "regex"; prepared once."""
    if not 0 <= tolerance <= MAX_TOLERANCE:
        raise ValueError("tolerance must be 0, 1 or 2")
    if mode == "regex":
        compiled = compile_pattern(query)
        return lambda text: regex(compiled, text)
    if mode == "fragment" and tolerance == 0:
        folded = fold(query)
        return lambda text: bool(folded) and folded in fold(text)
    prepared = _prepare(words_of(query), tolerance)
    if mode == "fragment":
        return lambda text: _all_fragments_found(prepared, set(words_of(text)))
    if mode == "words":
        return lambda text: _all_words_found(prepared, set(words_of(text)))
    raise ValueError(f"Unknown match mode: {mode!r}.")
