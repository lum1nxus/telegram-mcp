"""Query expansion for `search_messages`: pure functions, no I/O, deterministic.

Telegram matches the beginning of each word of a message and ANDs the words of a query, so a
search for "фруктовая компания" misses "фруктом". This module turns a query into a short
cascade of Telegram queries: word prefixes built from Snowball stems, the rarest pair and word,
spelling variants (other script, Polish letters without diacritics, "ё" as "е") and common
compounds. The server runs the cascade; see docs/history/2026-10-21-smart-search-v1-design.md.
"""

import re
import threading
from dataclasses import dataclass
from functools import cache
from typing import Literal, Protocol

import snowballstemmer

MAX_QUERIES = 6  # Telegram searches per tool call inside one chat
MAX_QUERIES_GLOBAL = 4  # the same for a global search, which is heavier for Telegram
MIN_PREFIX = 4  # a stem-based prefix shorter than this is not used; the whole word is
STRIP_FLOOR = 5  # a derivational tail is cut only if this many characters remain
MAX_TOKENS = 8  # words of a query used; the rest are left out
MAX_QUERY_CHARS = 200  # the joined query; words are dropped from the end to fit, never cut

Script = Literal["cyrillic", "latin", "other"]

# Short function words of Russian, Polish and English, lower-case.
STOP_WORDS: frozenset[str] = frozenset(
    {
        # Russian
        "и", "в", "во", "на", "по", "про", "о", "об", "обо", "от", "до", "за", "из", "к",
        "ко", "с", "со", "у", "не", "ни", "а", "но", "да", "или", "что", "как", "это", "для",
        "же", "ли", "бы", "то", "при", "под", "над",
        # Polish
        "i", "w", "we", "na", "o", "z", "ze", "do", "od", "po", "za", "u", "a", "ale", "lub",
        "czy", "nie", "to", "się", "jak", "że", "dla", "przy", "oraz",
        # English
        "the", "an", "of", "in", "on", "at", "for", "and", "or", "but", "is", "are", "was",
        "be", "by", "with", "from", "as", "it", "this", "that", "not", "no",
    }
)  # fmt: skip

COMPOUND_HEADS_CYRILLIC: tuple[str, ...] = (
    "авто", "микро", "мини", "макро", "супер", "бизнес", "гидро", "суб", "интер", "евро",
    "фото", "видео", "теле", "электро", "энерго", "агро", "эко", "био", "нано", "мульти",
)  # fmt: skip
COMPOUND_HEADS_LATIN: tuple[str, ...] = (
    "auto", "micro", "mini", "super", "sub", "inter", "euro", "eco", "bio", "e",
    "foto", "video", "tele", "multi",
)  # fmt: skip
MAX_COMPOUNDS = 20
MAX_VARIANTS = 3

# Derivational tails cut from a prefix, longest first. The spec's single-letter tails (Russian
# "н", "к", Polish "n") are left out: they would turn the measured "компан" into "компа" and
# "сантехник" into "сантехни".
_TAILS_CYRILLIC = ("изм", "ист", "ов", "ев", "ск")
_TAILS_LATIN = ("ow", "sk")

_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}  # fmt: skip
# The inverse, digraphs first. "e" is "э" at the start of a word and "е" elsewhere; "y" is "и".
_LATIN_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("shch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
    ("yu", "ю"), ("ya", "я"),
)  # fmt: skip
_LATIN_TO_CYRILLIC: dict[str, str] = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и",
    "y": "и", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р",
    "s": "с", "t": "т", "u": "у", "f": "ф",
}  # fmt: skip
_POLISH_PLAIN = str.maketrans("ąćęłńóśźż", "acelnoszz")

_WORD = re.compile(r"[\w@#]+")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[a-zA-ZÀ-ɏ]")


@dataclass(frozen=True)
class Token:
    text: str  # as written in the query
    lower: str
    script: Script
    position: int  # index of the word in the query, stop words and short words included


@dataclass(frozen=True)
class Plan:
    steps: tuple[
        tuple[str, str], ...
    ]  # (label, query); labels: exact, pair, single, variant, compound


class _Stemmer(Protocol):
    def stemWord(self, word: str) -> str: ...  # noqa: N802 - the library's name


# A Snowball stemmer keeps state between calls; tool calls may run in threads, so one at a time.
_STEM_LOCK = threading.Lock()


@cache
def _stemmer(language: str) -> _Stemmer:
    """One Snowball stemmer per language, created on first use."""
    stemmer: _Stemmer = snowballstemmer.stemmer(language)
    return stemmer


def _stem(word: str, language: str) -> str:
    with _STEM_LOCK:
        return _stemmer(language).stemWord(word)


def _script(word: str) -> Script:
    """Cyrillic or Latin; a word with letters of both, or of neither, is "other"."""
    cyrillic = _CYRILLIC.search(word) is not None
    latin = _LATIN.search(word) is not None
    if cyrillic != latin:
        return "cyrillic" if cyrillic else "latin"
    return "other"


def tokenize(query: str) -> list[Token]:
    """Words of 2+ characters with a letter or digit that are not stop words, each once (the
    first time it occurs, case ignored); `@name` and `#tag` stay one token."""
    tokens: list[Token] = []
    seen: set[str] = set()
    for position, text in enumerate(_WORD.findall(query)):
        lower = text.lower()
        if len(text) < 2 or lower in STOP_WORDS or lower in seen:  # noqa: PLR2004 - the spec
            continue
        if not any(ch.isalnum() and ch != "_" for ch in text):
            continue
        seen.add(lower)
        tokens.append(Token(text, lower, _script(text), position))
    return tokens


def _common_length(word: str, stem: str) -> int:
    """Length of the common prefix, with "ё" and "е" equal (the Russian stemmer writes "е")."""
    length = 0
    for a, b in zip(word.replace("ё", "е"), stem.replace("ё", "е"), strict=False):
        if a != b:
            break
        length += 1
    return length


def search_prefix(token: Token) -> str:
    """The word beginning to search for: common prefix of the word and its Snowball stem.

    Handles, tags, numbers and words with digits are searched as written. For Latin words the
    English and the Polish stems are both tried and the shorter common prefix of 4+ characters
    wins; below 4 characters the whole word is used.
    """
    word = token.lower
    if token.script == "other" or not word.isalpha() or len(word) < MIN_PREFIX:
        return word
    languages = ("russian",) if token.script == "cyrillic" else ("english", "polish")
    lengths = [_common_length(word, _stem(word, lang)) for lang in languages]
    usable = [n for n in lengths if n >= MIN_PREFIX]
    if not usable:
        return word
    prefix = word[: min(usable)]
    tails = _TAILS_CYRILLIC if token.script == "cyrillic" else _TAILS_LATIN
    for tail in tails:
        if prefix.endswith(tail) and len(prefix) - len(tail) >= STRIP_FLOOR:
            return prefix[: -len(tail)]
    return prefix


def _to_latin(word: str) -> str | None:
    letters = [_CYRILLIC_TO_LATIN.get(ch) for ch in word]
    if any(letter is None for letter in letters):
        return None
    return "".join(letter for letter in letters if letter is not None)


def _to_cyrillic(word: str) -> str | None:
    out: list[str] = []
    i = 0
    while i < len(word):
        digraph = next((pair for pair in _LATIN_DIGRAPHS if word.startswith(pair[0], i)), None)
        if digraph is not None:
            out.append(digraph[1])
            i += len(digraph[0])
            continue
        ch = word[i]
        if ch == "e" and i == 0:
            out.append("э")
        elif ch in _LATIN_TO_CYRILLIC:
            out.append(_LATIN_TO_CYRILLIC[ch])
        else:
            return None
        i += 1
    return "".join(out)


def variants(prefix: str, script: str) -> list[str]:
    """Other spellings of a prefix: other script, Polish letters plain, "ё" as "е" (at most 3)."""
    if script not in ("cyrillic", "latin") or not prefix.isalpha():
        return []
    candidates = [
        _to_latin(prefix) if script == "cyrillic" else _to_cyrillic(prefix),
        prefix.translate(_POLISH_PLAIN) if script == "latin" else None,
        prefix.replace("ё", "е") if script == "cyrillic" else None,
    ]
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate != prefix and candidate not in result:
            result.append(candidate)
    return result[:MAX_VARIANTS]


def compounds(prefix: str, script: str) -> list[str]:
    """The prefix behind common compound heads ("мини" + "трактор"), most common heads first."""
    if len(prefix) < MIN_PREFIX or not prefix.isalpha():
        return []
    if script == "cyrillic":
        heads = COMPOUND_HEADS_CYRILLIC
    elif script == "latin":
        heads = COMPOUND_HEADS_LATIN
    else:
        return []
    return [head + prefix for head in heads][:MAX_COMPOUNDS]


def _rarity(token: Token) -> tuple[bool, bool, int, int]:
    special = any(ch.isdigit() or ch in "@#" for ch in token.text)
    proper = token.position > 0 and token.text[:1].isupper()
    return (not special, not proper, -len(token.text), token.position)


def rank(tokens: list[Token]) -> list[Token]:
    """Rarest first: digits or @/#, then capitalised past the first word, then longer, earlier."""
    return sorted(tokens, key=_rarity)


def _fit(tokens: list[Token], prefixes: dict[int, str]) -> list[Token]:
    """At most MAX_TOKENS words, then words dropped from the end until the joined prefixes fit
    MAX_QUERY_CHARS; one word always stays, whole."""
    kept = tokens[:MAX_TOKENS]
    while len(kept) > 1 and len(" ".join(prefixes[t.position] for t in kept)) > MAX_QUERY_CHARS:
        kept.pop()
    return kept


def _candidates(query: str, tokens: list[Token]) -> list[tuple[str, str]]:
    if not tokens:
        return [("exact", query)]
    prefixes = {token.position: search_prefix(token) for token in tokens[:MAX_TOKENS]}
    tokens = _fit(tokens, prefixes)
    ranked = rank(tokens)
    rarest = ranked[0]
    steps = [("exact", " ".join(prefixes[t.position] for t in tokens))]
    if len(tokens) >= 3:  # noqa: PLR2004 - with two words the pair is the exact query
        pair = sorted(ranked[:2], key=lambda t: t.position)
        steps.append(("pair", " ".join(prefixes[t.position] for t in pair)))
    single = prefixes[rarest.position]
    steps.append(("single", single))
    steps += [("variant", v) for v in variants(single, rarest.script)]
    steps += [("compound", c) for c in compounds(single, rarest.script)]
    return steps


def plan(query: str, *, budget: int = MAX_QUERIES) -> Plan:
    """The search cascade for a query, at most `budget` steps, no query twice.

    An empty query gives an empty plan (the server rejects it); a query of stop words only is
    searched as written. Only the first MAX_TOKENS words are used, fewer if their joined
    prefixes pass MAX_QUERY_CHARS.
    """
    text = query.strip()
    if not text:
        return Plan(())
    steps: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, q in _candidates(text, tokenize(text)):
        if len(steps) >= budget:
            break
        if q in seen:
            continue
        seen.add(q)
        steps.append((label, q))
    return Plan(tuple(steps))
