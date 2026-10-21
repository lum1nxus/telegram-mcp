"""The deep-scan matching module: folding, fragments, words, fuzzy words and safe regex."""

import re

import pytest

from tgmcp.match import (
    LARGE_REPEAT,
    MAX_PATTERN_CHARS,
    MAX_TEXT_CHARS_MULTI,
    MAX_TEXT_CHARS_TRIPLE,
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


def test_fuzzy_word_short_words_skip_the_trigram_gate() -> None:
    assert fuzzy_word("kota", "kata", 1)
    assert fuzzy_word("кран", "крон", 1)
    assert fuzzy_word("dach", "dech", 1)
    assert not fuzzy_word("kosa", "kasza", 1)  # distance 2
    assert not fuzzy_word("tomek", "tomke", 2)  # a transposition is 2 edits; clamped to 1


def test_fuzzy_word_long_words_pass_the_gate() -> None:
    assert not fuzzy_word("tractor", "zzzzzzz", 2)
    assert fuzzy_word("kosiarka", "kosiarke", 2)


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
    assert compiled.unbounded == 0
    assert regex(compiled, "x" * 4090 + "needle")
    assert not regex(compiled, "x" * 4096 + "needle")
    one = compile_pattern(r"needle\w+")
    assert one.unbounded == 1
    assert regex(one, "x" * 2000 + "needles")


def test_two_unbounded_quantifiers_match_the_first_1024_characters() -> None:
    assert MAX_TEXT_CHARS_MULTI == 1024
    compiled = compile_pattern("teplic.*cena.*zł")
    assert compiled.unbounded == 2
    assert regex(compiled, "teplica, cena 100 zł " + "x" * 1000)
    assert not regex(compiled, "x" * 1024 + "teplica, cena 100 zł")
    assert compile_pattern(".*teplic.*cena").unbounded == 1  # the leading .* is stripped
    assert regex(compiled, "x" * 1000 + "teplica, cena 100 zł")


def test_disjoint_pairs_do_not_lower_the_text_cap() -> None:
    compiled = compile_pattern(r"\d+\s*zł")
    assert compiled.unbounded == 1
    assert regex(compiled, "cena " + "x" * 1500 + " 1234 zł")
    overlapping = compile_pattern("a.*a.*c")
    assert overlapping.unbounded == 2
    assert regex(overlapping, "x" * 1000 + "aac")
    assert not regex(overlapping, "x" * 1100 + "aac")


@pytest.mark.parametrize("pattern", ["a{2,3}b", r"\d{1,4} zł", ".{0,20}cena", "a{1,20}b"])
def test_small_bounded_repeats_stay_bounded(pattern: str) -> None:
    assert compile_pattern(pattern).unbounded == 0


def test_large_bounded_repeats_count_as_unbounded() -> None:
    assert LARGE_REPEAT == 20
    compiled = compile_pattern(".{0,500}a.{0,500}a.{0,500}c")
    assert compiled.unbounded == 3
    assert compile_pattern("teplic.{0,21}cena").unbounded == 1
    assert compile_pattern("teplic.{,50}?cena").unbounded == 1
    for pattern in ("a{1,200}a{1,200}a{1,200}c", "a{0,30}+a*c", "teplic.{0,50}.{0,50}cena"):
        with pytest.raises(PatternRefused, match="could run forever"):
            compile_pattern(pattern)


def test_three_unbounded_quantifiers_match_the_first_256_characters() -> None:
    assert MAX_TEXT_CHARS_TRIPLE == 256
    compiled = compile_pattern(r"teplic.*cena.*dach\w*")
    assert compiled.unbounded == 3
    assert regex(compiled, "x" * 200 + "teplica, cena, dachy " + "x" * 2000)
    assert not regex(compiled, "x" * 300 + "teplica, cena, dachy " + "x" * 2000)


@pytest.mark.parametrize(
    "pattern",
    [
        "мини.?трактор",
        r"\bteplic\w*",
        "(ab)+c",
        "a{2,3}b",
        "теплиц.*цена",
        "cena|price",
        "Zielon.? firma",
        r"\*+ ogłoszenie",
        "[*+]+x",
    ],
)
def test_legitimate_patterns_compile(pattern: str) -> None:
    compiled = compile_pattern(pattern).pattern
    assert isinstance(compiled, re.Pattern)
    assert compiled.flags & re.IGNORECASE


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+$",
        "(.*)*",
        "(x|y*)*",
        r"(\w+)\1",
        "(a+){2,}",
        r"(?P<g>a)(?P=g)",
        # a quantified group holding a quantifier, bounded counts included
        "(a?){25}",
        "(a{2,10}){2,10}",
        "(.*a){20}",
        "((ab)+)+",
        # a quantified group holding an alternation
        "(a|aa)+",
        "(a|a?)*",
        "(a|ab){2,}",
        # refused although harmless (accepted cost of the heuristic)
        "(a+)?",
        "((a|b)c)+",
        # runs seen through unquantified groups
        "(.*)(.*)цена",
        "(.*)(.*)=",
        "(?:.*)(?:.*)=",
        "(a*)(a*)b",
        "(?:x*)x*=",
        "x*()x*=",
        # an optional atom does not break a run
        "a*b?a*c",
        "x*(y)?x*=",
        r"\w*\s?\w*!",
        # overlapping classes: the same class, or reachable through an atom that may be empty
        r"\d+\d*",
        r"\d+[.,]?\d*",  # `[.,]?` may be empty, so `\d+` meets `\d*`
        r"\d+\s*\d+",
        "a*a*b",
        r"\s*\s*цена",
        "[а-я]*[а-я ]*X",
        r"\w+-*\w*",
        r"\s+ +",
        # zero-width constructs do not break a run; comments and verbose mode are refused
        "x*(?=x)x*=",
        "a*(?=a)a*=",
        "1*(?=1)1*=",
        r"\s*(?<=\s)\s*=",
        r"\d*\B\d*=",
        "x*(?#c)x*=",
        "(?x)x* x*=",
        r"(?x)\d+ \d+=",
        "(?ix)teplic",
        "(?x:teplic)",
        "teplic(?#c)",
        # a {0} atom, an empty branch and a conditional match nothing: they must not hide a run
        "x*y{0}x*=",
        "x*(?:y){0}x*=",
        "x*(|y)x*=",
        "x*(?:y|)x*=",
        "a||b",
        "()x*(?(1)|)x*=",
        "(x)?x*(?(1)|)x*=",
        # an unbounded atom at an end of an alternation branch meets one outside the group
        "(a*|b)a*c",
        "(b|xa*)a*c",
        "a*(a*|b)c",
        "a*(?:b|a+x)c",
        "(a*|(b|a*))a*c",
        # two unbounded atoms in a row
        "a*a*",
        ".*.*",
        r"\s*\s*",
        "[а-я]*[а-я ]*",
        r"\w+\w*",
        "a+?a{2,}",
        # more than three unbounded quantifiers
        "a+-b+-c+-d+",
        "teplic.*cena.*dach.*kosa.*grabie",
    ],
)
def test_catastrophic_patterns_are_refused(pattern: str) -> None:
    with pytest.raises(PatternRefused, match="could run forever"):
        compile_pattern(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "a+.a+.a+c",
        ".*a.*a.*c",
        "(?=a+)a*",
        # disjoint neighbours do not form a run
        r"\d+\s*zł",
        r"\w+\s*цена",
        r"(\d+)\s*(руб|zł)",
        r"\d+\s*-\s*\d",
        r"\S+\s+\S+ цена",
        r".*?\d+ ?zł",
        r"\d+-+",
        r"\s*z+",
        # zero-width atoms are allowed when there is no run
        r"\bteplic\w*",
        "^цена",
        "цена$",
        r"\Ateplic\b",
        r"x*\bx",
        # alternation groups whose branches cannot meet the run outside
        "(a|b)a*c",
        "(a*x|b)a*c",
        "a*(xa*|b)c",
        r"(\d+|\s)\s*zł",
    ],
)
def test_literal_separated_runs_are_allowed(pattern: str) -> None:
    compile_pattern(pattern)


def test_three_unbounded_quantifiers_are_allowed() -> None:
    compile_pattern("a+-b+-c+")
    compile_pattern(".*-a+-b+-c+")  # the leading .* is stripped before counting


def test_leading_any_is_stripped() -> None:
    assert compile_pattern(".*abc").pattern.pattern == "abc"
    assert compile_pattern(".*?abc").pattern.pattern == "abc"
    assert compile_pattern("(?s).*abc").pattern.pattern == "(?s)abc"
    assert compile_pattern("(?s).*?теплиц").pattern.pattern == "(?s)теплиц"
    assert compile_pattern(".+abc").pattern.pattern == ".+abc"  # needs a character before abc
    assert compile_pattern("^.*abc").pattern.pattern == "^.*abc"  # first line only: kept
    assert compile_pattern("^.+abc").pattern.pattern == "^.+abc"
    compiled = compile_pattern(".*теплиц.*цена")
    assert compiled.pattern.pattern == "теплиц.*цена"
    assert regex(compiled, "у нас теплица, цена 100")


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


@pytest.mark.parametrize("tolerance", [-1, 3])
def test_matcher_refuses_tolerance_out_of_range(tolerance: int) -> None:
    with pytest.raises(ValueError, match="tolerance must be 0, 1 or 2"):
        matcher("words", "teplica", tolerance)


def test_deterministic() -> None:
    text = "Garden Club: Zażółć теплицу, Ёлка"
    for _ in range(3):
        assert fold(text) == "garden club: zazolc теплицу, елка"
        assert levenshtein("trakcor", "tractor", 2) == 2
        assert fragment_fuzzy("zazolc teplicu", text, 1) is fragment_fuzzy(
            "zazolc teplicu", text, 1
        )
