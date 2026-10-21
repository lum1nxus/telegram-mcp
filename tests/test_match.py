"""The deep-scan matching module: folding, fragments, words, fuzzy words and safe regex."""

import re

import pytest

from tgmcp.match import (
    MAX_PATTERN_CHARS,
    PatternRefused,
    clamp_tolerance,
    compile_pattern,
    fold,
    fragment,
    fragment_fuzzy,
    fuzzy_word,
    levenshtein,
    matcher,
    regex,
    trigrams,
    words,
    words_of,
)


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Zażółć", "zazolc"),
        ("Zażółć gęślą jaźń", "zazolc gesla jazn"),
        ("naïve", "naive"),
        ("Straße", "strasse"),
        ("теплицей", "теплицей"),
        ("Ёлка", "елка"),
        ("Łódź", "lodz"),
        ("TEPLICA", "teplica"),
        ("Теплица", "теплица"),
        ("Café", "cafe"),
        ("", ""),
    ],
)
def test_fold_table(text: str, folded: str) -> None:
    assert fold(text) == folded


def test_fold_keeps_cyrillic_short_i() -> None:
    assert fold("Чайник") == "чайник"
    assert fold("й") != fold("и")


def test_words_of_splits_on_non_word_characters() -> None:
    assert words_of("Tomek, поделился: теплицей!") == ["tomek", "поделился", "теплицей"]


def test_trigrams_padding_and_short_words() -> None:
    assert trigrams("") == frozenset()
    assert trigrams("a") == frozenset()
    assert trigrams("ab") == frozenset({"  a", " ab", "ab "})


def test_levenshtein() -> None:
    assert levenshtein("kitten", "sitting", 3) == 3
    assert levenshtein("kitten", "sitting", 2) == 3
    assert levenshtein("", "abc", 5) == 3
    assert levenshtein("abc", "abc", 0) == 0
    assert levenshtein("abc", "", 1) == 2


@pytest.mark.parametrize(
    ("word", "tolerance", "clamped"),
    [("abc", 2, 0), ("abcd", 2, 1), ("abcdef", 2, 1), ("abcdef", 0, 0), ("abcdefg", 2, 2)],
)
def test_clamp_tolerance(word: str, tolerance: int, clamped: int) -> None:
    assert clamp_tolerance(word, tolerance) == clamped


def test_fuzzy_word() -> None:
    assert fuzzy_word("tracto", "tractor", 1)
    assert fuzzy_word("trackor", "tractor", 1)  # one substitution: k for t
    assert not fuzzy_word("trakcor", "tractor", 1)  # two substitutions
    assert fuzzy_word("trakcor", "tractor", 2)
    assert not fuzzy_word("track", "tractor", 2)  # clamped to 1, distance 2
    assert not fuzzy_word("abc", "abd", 2)  # clamped to 0
    assert fuzzy_word("abc", "abc", 2)
    assert not fuzzy_word("tractor", "garden", 2)  # false positive must not match


def test_fragment_inside_a_compound() -> None:
    assert fragment("трактор", "взяли минитрактор")
    assert fragment("ТРАКТОР", "взяли Минитрактор")
    assert not fragment("", "взяли минитрактор")
    assert not fragment("комбайн", "взяли минитрактор")


def test_words_any_order_and_whole_words() -> None:
    assert words("теплицей tomek", "Tomek поделился теплицей")
    assert not words("tomek теплиц", "Tomek поделился теплицей")  # a prefix is not a word
    assert not words("", "Tomek поделился теплицей")
    assert words("tomek теплицай", "Tomek поделился теплицей", 1)


def test_fragment_fuzzy() -> None:
    assert fragment_fuzzy("tomek теплиц", "Tomek поделился теплицей", 1)
    assert fragment_fuzzy("tomak", "Tomek поделился теплицей", 2)  # clamped to 1
    assert not fragment_fuzzy("", "Tomek поделился теплицей", 1)
    assert not fragment_fuzzy("tomek kosiarka", "Tomek поделился теплицей", 1)


def test_regex_ignorecase_on_original_text() -> None:
    assert regex(compile_pattern("мини.?трактор"), "взяли МИНИТРАКТОР")
    assert not regex(compile_pattern("lodz"), "Łódź")  # no folding for regex


def test_regex_scans_at_most_4096_characters() -> None:
    compiled = compile_pattern("needle")
    assert regex(compiled, "x" * 4090 + "needle")
    assert not regex(compiled, "x" * 4096 + "needle")


@pytest.mark.parametrize("pattern", ["мини.?трактор", r"\bteplic\w*", "(ab)+c", "a{2,3}b"])
def test_legitimate_patterns_compile(pattern: str) -> None:
    compiled = compile_pattern(pattern)
    assert isinstance(compiled, re.Pattern)
    assert compiled.flags & re.IGNORECASE


@pytest.mark.parametrize(
    "pattern", ["(a+)+$", "(.*)*", "(x|y*)*", r"(\w+)\1", "(a+){2,}", r"(?P<g>a)(?P=g)"]
)
def test_catastrophic_patterns_are_refused(pattern: str) -> None:
    with pytest.raises(PatternRefused, match="could run forever"):
        compile_pattern(pattern)


def test_long_pattern_is_refused() -> None:
    assert MAX_PATTERN_CHARS == 200
    compile_pattern("a" * 200)
    with pytest.raises(PatternRefused, match="longer than 200"):
        compile_pattern("a" * 201)


def test_invalid_pattern_is_refused() -> None:
    with pytest.raises(PatternRefused, match="Not a valid pattern"):
        compile_pattern("(unclosed")
    assert issubclass(PatternRefused, ValueError)


def test_matcher_modes() -> None:
    text = "Tomek поделился теплицей и минитрактором"
    assert matcher("fragment", "трактор", 0)(text)
    assert not matcher("fragment", "tomek теплиц", 0)(text)
    assert matcher("fragment", "tomek теплиц", 1)(text)
    assert matcher("words", "теплицей tomek", 0)(text)
    assert not matcher("words", "трактор", 0)(text)
    assert matcher("regex", r"мини\w*", 0)(text)
    with pytest.raises(PatternRefused):
        matcher("regex", "(a+)+", 0)
    with pytest.raises(ValueError, match="mode"):
        matcher("glob", "x", 0)


def test_deterministic() -> None:
    text = "Garden Club: Zażółć теплицу, Ёлка"
    for _ in range(3):
        assert fold(text) == "garden club: zazolc теплицу, елка"
        assert levenshtein("trakcor", "tractor", 2) == 2
        assert fragment_fuzzy("zazolc teplicu", text, 1) is fragment_fuzzy(
            "zazolc teplicu", text, 1
        )
