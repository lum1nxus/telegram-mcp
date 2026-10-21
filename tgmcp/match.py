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
_LEADING_ANY = re.compile(r"(\(\?[aiLmsux]+\))?\.\*\??(?![*+?{]|\.[*+])")
_VERBOSE_OR_COMMENT = re.compile(r"\(\?(?:#|[aiLmsux-]*x[aiLmsux-]*[:)])")
# Blanking: an escape or a character class becomes one atom. The six shorthand classes keep
# an identity (private-use characters) so that provably disjoint neighbours can be told apart;
# the zero-width assertions \b \B \A \Z get their own marker; every other escape and every
# bracket class becomes the opaque atom.
_OPAQUE = "\ue010"
_ZERO_WIDTH = "\ue011"
_ASSERTIONS = frozenset("bBAZ")
_SHORTHAND = {
    "d": "\ue000",
    "D": "\ue001",
    "s": "\ue002",
    "S": "\ue003",
    "w": "\ue004",
    "W": "\ue005",
}
_DIGIT, _NOT_DIGIT = _SHORTHAND["d"], _SHORTHAND["D"]
_SPACE, _NOT_SPACE = _SHORTHAND["s"], _SHORTHAND["S"]
_WORD_CLASS, _NOT_WORD = _SHORTHAND["w"], _SHORTHAND["W"]
_SEPARATE_PAIRS = frozenset(
    frozenset(pair)
    for pair in (
        (_DIGIT, _SPACE),
        (_DIGIT, _NOT_DIGIT),
        (_SPACE, _NOT_SPACE),
        (_WORD_CLASS, _NOT_WORD),
        (_WORD_CLASS, _SPACE),
    )
)
_NOT_PLAIN = frozenset({*_SHORTHAND.values(), _OPAQUE, _ZERO_WIDTH, ".", "^", "$", "|", ""})
_CONSUMES_NOTHING = frozenset({_ZERO_WIDTH, "^", "$"})
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
    atom: str = ""  # the blanked character of a plain atom; "" for a group


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
        atom = "" if child is not None else c
        group.items.append(_Item(child, quantifier.group() if quantifier else "", atom))
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


def _run(group: _Group) -> Iterator[tuple[str, str]]:
    """The (atom, quantifier) sequence of the group, with plain groups flattened.

    An unquantified group without `|` that is not a lookaround is spliced into the sequence
    (an empty group vanishes); a lookaround counts as the zero-width marker; any other group
    counts as one atom with its quantifier.
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
        elif inner is not None and inner.lookaround:
            yield _ZERO_WIDTH, item.quantifier
        else:
            yield item.atom, item.quantifier


def _never_overlap(a: str, b: str) -> bool:
    """Whether two blanked atoms provably match no common character (else: they overlap)."""
    if frozenset((a, b)) in _SEPARATE_PAIRS:
        return True
    for cls, literal in ((a, b), (b, a)):
        if literal not in _NOT_PLAIN and (
            (cls == _DIGIT and not literal.isdigit()) or (cls == _SPACE and not literal.isspace())
        ):
            return True
    return False


def _has_unbounded_in_a_row(group: _Group) -> bool:
    """Two overlapping unbounded atoms with nothing between them that must match.

    Atoms that may match nothing (`*`, `?`) let the atoms on both sides meet, so an unbounded
    atom is compared with every unbounded atom it can meet, not just the previous one. A
    zero-width atom (`^`, `$`, `\\b`, `\\B`, `\\A`, `\\Z`, an unquantified lookaround) consumes
    nothing, so it does not break a run either.
    """
    reachable: list[str] = []  # unbounded atoms the next atom can be adjacent to
    for atom, quantifier in _run(group):
        nullable = quantifier.startswith(("*", "?", "{0,", "{,")) or (
            not quantifier and atom in _CONSUMES_NOTHING
        )
        if _is_unbounded(quantifier):
            if any(not _never_overlap(seen, atom) for seen in reachable):
                return True
            reachable = [*reachable, atom] if nullable else [atom]
        elif not nullable:
            reachable = []
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


def _blank_escape(letter: str) -> str:
    if letter in _ASSERTIONS:
        return _ZERO_WIDTH
    return _SHORTHAND.get(letter, _OPAQUE)


def _blank(pattern: str) -> str:
    """The pattern with escapes and character classes replaced by one-character atoms."""
    escaped = _ESCAPE.sub(lambda m: _blank_escape(m.group()[1]), pattern)
    return _CHAR_CLASS.sub(_OPAQUE, escaped)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a scan pattern with IGNORECASE, refusing what could run forever.

    The guard is a heuristic, not a proof. Escapes and character classes count as one plain
    atom, so `\\*`, `[*+]` and `\\(` are not taken for syntax. In this order:
    - a pattern longer than `MAX_PATTERN_CHARS` characters is refused;
    - back-references `\\1`...`\\9` and `(?P=name)` are refused;
    - comments `(?#...)` and the verbose flag `x` in any inline flag group (`(?x)`, `(?ix)`,
      `(?x:...)`) are refused: ignored whitespace would hide a run (`(?x)x* x*=`);
    - any quantifier on a group that contains a quantifier, bounded counts and `?` included:
      `(a+)+`, `(a?){25}`, `(a{2,10}){2,10}`, `(.*a){20}`, `((ab)+)+` are refused;
    - any quantifier on a group that contains `|`, at any depth: `(a|aa)+`, `(a|ab){2,}` are
      refused (`cena|price` at the top level passes);
    - one leading `.*` or lazy `.*?`, also right after an inline flag group at the very start,
      is stripped unless another `.*`/`.+` follows: under `search` it changes nothing, so
      `.*abc` and `.*?abc` compile as `abc` and `(?s).*abc` as `(?s)abc`; `.+abc` and
      `^.*abc` are kept as given; the checks below run on the stripped pattern;
    - two unbounded atoms in a row (`*`, `+`, `{n,}`, lazy forms included) are refused:
      `a*a*`, `.*.*`, `\\w+\\w*`. Unquantified plain groups are seen through (`(.*)(.*)=`,
      `x*()x*=`) and an atom that may match nothing does not break the run (`a*b?a*c`,
      `x*(y)?x*=`, `\\d+\\s*\\d+`), nor does a zero-width atom: `^`, `$`, `\\b`, `\\B`,
      `\\A`, `\\Z` or an unquantified lookaround (`x*(?=x)x*=`, `\\d*\\B\\d*=`).
      Exception: atoms whose characters provably never overlap do not form a run; only the
      shorthand pairs `\\d`/`\\s`, `\\d`/`\\D`, `\\s`/`\\S`, `\\w`/`\\W`, `\\w`/`\\s`, and
      `\\d` or `\\s` next to one plain character it cannot match, count as disjoint
      (`\\d+\\s*zł`, `\\d+-+`);
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
    if _BACK_REFERENCE.search(pattern) or _VERBOSE_OR_COMMENT.search(_blank(pattern)):
        raise PatternRefused(_FOREVER)
    leading = _LEADING_ANY.match(pattern)
    stripped = (leading.group(1) or "") + pattern[leading.end() :] if leading else pattern
    tree = _parse(_blank(stripped))[0]
    if _is_risky(tree) or sum(_is_unbounded(q) for q in _quantifiers(tree)) > MAX_UNBOUNDED:
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
