"""Matching for the deep scan: folding, fragments, whole words, fuzzy words and safe regex.

Pure and deterministic: no I/O, no state. The scan builds one predicate per call with
`matcher` (the query is folded and the pattern compiled once) and applies it to each message.

- `fold` lower-cases and maps an explicit table of accented letters (Polish, ё→е, common Latin
  accents, ß→ss), so "Zażółć" and "zazolc" compare equal; every other letter is kept (й stays
  й, distinct from и).
- `fragment` is a folded substring ("трактор" finds "минитрактор").
- `words` needs every query word to be a whole word of the text, in any order.
- `tolerance` (ruling 3) compares each query word with whole text words: a trigram Jaccard
  gate, then Levenshtein. It is clamped by query-word length (ruling 6): up to 3 letters → 0,
  4-6 letters → at most 1, 7 and more → as given. In `fragment` mode with tolerance > 0 a query
  word matches a text word that it fuzzy-matches or is an exact substring of; a typo inside a
  compound is not caught.
- `regex` (ruling 4) searches the ORIGINAL text, case-insensitive, on at most 4 096
  characters; `compile_pattern` refuses patterns that could run forever (see its docstring).
"""

import re
from collections.abc import Callable

MAX_PATTERN_CHARS = 200
MAX_TEXT_CHARS = 4096
MIN_JACCARD = 0.3
MIN_TRIGRAM_WORD = 2  # shorter words have no trigrams
SHORT_WORD = 3  # up to this many letters: no tolerance
MEDIUM_WORD = 6  # up to this many letters: tolerance at most 1

_WORD = re.compile(r"\w+")
_BACK_REFERENCE = re.compile(r"\\[1-9]|\(\?P=")
_ESCAPE = re.compile(r"\\.", re.DOTALL)
_CHAR_CLASS = re.compile(r"\[\^?\]?[^\]]*\]")
_UNBOUNDED = re.compile(r"[+*]|\{\d*,\}")

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


def fuzzy_word(query_word: str, text_word: str, tolerance: int) -> bool:
    """Whether two folded words are within the clamped tolerance (trigram gate, then edits)."""
    tol = clamp_tolerance(query_word, tolerance)
    if tol == 0:
        return query_word == text_word
    if abs(len(query_word) - len(text_word)) > tol:
        return False
    q_grams, t_grams = trigrams(query_word), trigrams(text_word)
    if q_grams and t_grams:
        jaccard = len(q_grams & t_grams) / len(q_grams | t_grams)
        if jaccard < MIN_JACCARD:
            return False
    return levenshtein(query_word, text_word, tol) <= tol


def _all_words_found(query_words: list[str], text_words: list[str], tolerance: int) -> bool:
    if not query_words:
        return False
    if tolerance == 0:
        present = set(text_words)
        return all(q in present for q in query_words)
    return all(any(fuzzy_word(q, t, tolerance) for t in text_words) for q in query_words)


def _all_fragments_found(query_words: list[str], text_words: list[str], tolerance: int) -> bool:
    if not query_words:
        return False
    return all(any(q in t or fuzzy_word(q, t, tolerance) for t in text_words) for q in query_words)


def fragment(query: str, text: str) -> bool:
    """Whether the folded query is a substring of the folded text; an empty query never is."""
    folded = fold(query)
    return bool(folded) and folded in fold(text)


def words(query: str, text: str, tolerance: int = 0) -> bool:
    """Whether every query word matches a whole text word (fuzzy when tolerance > 0)."""
    return _all_words_found(words_of(query), words_of(text), tolerance)


def fragment_fuzzy(query: str, text: str, tolerance: int) -> bool:
    """Whether every query word is a substring of a text word or fuzzy-matches one."""
    return _all_fragments_found(words_of(query), words_of(text), tolerance)


def _has_nested_quantifier(pattern: str) -> bool:
    """Whether a group holding an unbounded quantifier is itself repeated without bound."""
    simple = _CHAR_CLASS.sub("x", _ESCAPE.sub("x", pattern))
    stack = [False]  # per open group: does it hold an unbounded quantifier?
    for i, c in enumerate(simple):
        if c == "(":
            stack.append(False)
        elif c == ")" and len(stack) > 1:
            inner = stack.pop()
            repeated = _UNBOUNDED.match(simple, i + 1) is not None
            if inner and repeated:
                return True
            stack[-1] = stack[-1] or inner or repeated
        elif _UNBOUNDED.match(simple, i):
            stack[-1] = True
    return False


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a scan pattern with IGNORECASE, refusing what could run forever.

    The guard is a heuristic, not a proof. Refused, in this order:
    - a pattern longer than `MAX_PATTERN_CHARS` characters;
    - back-references `\\1`...`\\9` and `(?P=name)`;
    - nested quantifiers: a group that holds `+`, `*` or `{n,}` (escapes and character
      classes ignored), itself followed by `+`, `*` or `{n,}`, lazy forms included:
      `(a+)+`, `(.*)*`, `(x|y*)*`, `((ab)+)+`; `(ab)+` and `(a+)?` pass;
    - anything `re` cannot compile.
    """
    if len(pattern) > MAX_PATTERN_CHARS:
        raise PatternRefused(_TOO_LONG)
    if _BACK_REFERENCE.search(pattern) or _has_nested_quantifier(pattern):
        raise PatternRefused(_FOREVER)
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise PatternRefused(f"Not a valid pattern: {e}") from e


def regex(compiled: re.Pattern[str], text: str) -> bool:
    """Whether the pattern occurs in the first `MAX_TEXT_CHARS` characters of the original text."""
    return compiled.search(text[:MAX_TEXT_CHARS]) is not None


def matcher(mode: str, query: str, tolerance: int) -> Callable[[str], bool]:
    """The predicate for one scan: "fragment", "words" or "regex"; prepared once."""
    if mode == "regex":
        compiled = compile_pattern(query)
        return lambda text: regex(compiled, text)
    if mode == "fragment" and tolerance == 0:
        folded = fold(query)
        return lambda text: bool(folded) and folded in fold(text)
    query_words = words_of(query)
    if mode == "fragment":
        return lambda text: _all_fragments_found(query_words, words_of(text), tolerance)
    if mode == "words":
        return lambda text: _all_words_found(query_words, words_of(text), tolerance)
    raise ValueError(f"Unknown match mode: {mode!r}.")
