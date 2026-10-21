"""Query expansion: prefixes from real Snowball stems, variants, compounds and the cascade plan."""

import threading

import pytest

from tgmcp import expand
from tgmcp.expand import (
    COMPOUND_HEADS_CYRILLIC,
    COMPOUND_HEADS_LATIN,
    MAX_QUERIES,
    MAX_QUERIES_GLOBAL,
    STOP_WORDS,
    Plan,
    Token,
    compounds,
    plan,
    rank,
    search_prefix,
    tokenize,
    variants,
)


def _token(word: str) -> Token:
    (token,) = tokenize(word)
    return token


def _labels(p: Plan) -> list[str]:
    return [label for label, _ in p.steps]


def _queries(p: Plan) -> list[str]:
    return [query for _, query in p.steps]


def test_constants() -> None:
    assert (MAX_QUERIES, MAX_QUERIES_GLOBAL, expand.MIN_PREFIX, expand.STRIP_FLOOR) == (6, 4, 4, 5)
    assert all(word == word.lower() for word in STOP_WORDS)
    assert {"и", "в", "на", "про", "the", "a", "of", "w", "na", "o"} <= STOP_WORDS


def test_compound_heads_are_the_spec_lists() -> None:
    assert COMPOUND_HEADS_CYRILLIC == (
        "авто", "микро", "мини", "макро", "супер", "бизнес", "гидро", "суб", "интер", "евро",
        "фото", "видео", "теле", "электро", "энерго", "агро", "эко", "био", "нано", "мульти",
    )  # fmt: skip
    assert COMPOUND_HEADS_LATIN == (
        "auto", "micro", "mini", "super", "sub", "inter", "euro", "eco", "bio", "e",
        "foto", "video", "tele", "multi",
    )  # fmt: skip


# --- tokenize ---------------------------------------------------------------------------------


def test_tokenize_drops_stop_words_and_keeps_handles() -> None:
    tokens = tokenize("про теплицы и @green_co")
    assert [t.text for t in tokens] == ["теплицы", "@green_co"]
    assert [t.position for t in tokens] == [1, 3]
    assert [t.script for t in tokens] == ["cyrillic", "latin"]


def test_tokenize_splits_punctuation_and_drops_short_tokens() -> None:
    tokens = tokenize("Garden, firma! x #auto 2024 🙂")
    assert [t.text for t in tokens] == ["Garden", "firma", "#auto", "2024"]
    assert [t.lower for t in tokens] == ["garden", "firma", "#auto", "2024"]
    assert [t.script for t in tokens] == ["latin", "latin", "latin", "other"]


def test_tokenize_mixed_script_word_is_other() -> None:
    token = _token("Ерstе")  # Cyrillic "Е", "р", "е" around Latin "st"
    assert token.script == "other"
    assert search_prefix(token) == "ерstе"
    assert variants(search_prefix(token), token.script) == []
    assert plan("Ерstе") == Plan((("exact", "ерstе"),))


def test_tokenize_needs_a_letter_or_digit() -> None:
    assert [t.text for t in tokenize("@@ ## __ #_ трактор @a1")] == ["трактор", "@a1"]


def test_tokenize_drops_repeated_words_keeping_the_first() -> None:
    tokens = tokenize("Garden трактор garden ТРАКТОР firma")
    assert [t.text for t in tokens] == ["Garden", "трактор", "firma"]
    assert [t.position for t in tokens] == [0, 1, 4]


def test_plan_caps_tokens_at_eight() -> None:
    words = ["alfa", "bravo", "delta", "echo", "golf", "hotel", "india", "juliet", "kilo", "lima"]
    exact = plan(" ".join(words)).steps[0]
    assert exact[0] == "exact"
    assert len(exact[1].split()) == expand.MAX_TOKENS == 8
    assert "kilo" not in exact[1] and "lima" not in exact[1]


def test_plan_caps_the_exact_query_at_200_characters_by_dropping_words() -> None:
    words = [str(digit) * 30 for digit in range(1, 9)]  # 8 numbers of 30 digits: 247 characters
    steps = plan(" ".join(words)).steps
    exact = steps[0][1]
    assert len(exact) <= expand.MAX_QUERY_CHARS == 200
    assert exact.split() == words[:6]  # words are dropped from the end, never cut
    assert all(len(query) <= 200 for _, query in steps)


def test_plan_keeps_one_long_word_whole() -> None:
    word = "7" * 250
    assert plan(word).steps[0] == ("exact", word)


def test_stemmer_is_safe_across_threads() -> None:
    words = ["фруктовая", "компании", "planting", "hydraulikiem", "odmówiła"] * 40
    expected = [search_prefix(_token(w)) for w in words]
    results: list[list[str]] = []

    def work() -> None:
        results.append([search_prefix(_token(w)) for w in words])

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [expected] * 8
    assert isinstance(expand._STEM_LOCK, type(threading.Lock()))


def test_tokenize_empty() -> None:
    assert tokenize("") == []
    assert tokenize("  и в  ") == []


# --- search_prefix ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word,prefix",
    [
        ("фруктовая", "фрукт"),  # stem "фруктов": the tail "ов" is cut
        ("фруктом", "фрукт"),
        ("фрукт", "фрукт"),
        ("садовая", "садов"),  # stem "садов": cutting "ов" would leave fewer than 5 letters
        ("компания", "компан"),
        ("компании", "компан"),
        ("минитрактор", "минитрактор"),
        ("сантехника", "сантехник"),
        ("заявка", "заявк"),
        ("подвала", "подва"),
        ("Томек", "томек"),
        ("planting", "plant"),
        ("plants", "plant"),
        ("company", "compan"),
        ("companies", "compani"),
        ("granite", "granit"),
        ("hydraulikiem", "hydraulik"),
        ("odmówiła", "odmów"),
        ("firma", "firm"),
        ("mowing", "mowing"),  # the English stem "mow" is under 4 letters: the word stays
        ("Zielona", "zielon"),
    ],
)
def test_search_prefix(word: str, prefix: str) -> None:
    assert search_prefix(_token(word)) == prefix


def test_search_prefix_folds_yo_when_comparing_with_the_stem() -> None:
    # The Russian stemmer writes "е" for "ё"; the prefix keeps the token's own letters.
    assert search_prefix(_token("ёлочками")) == "ёлочк"  # stem "елочк"
    assert search_prefix(_token("ёжиками")) == "ёжик"  # stem "ежик"


def test_search_prefix_leaves_handles_numbers_and_short_words() -> None:
    assert search_prefix(_token("@green_co")) == "@green_co"
    assert search_prefix(_token("#garden")) == "#garden"
    assert search_prefix(_token("2024")) == "2024"
    assert search_prefix(_token("кот")) == "кот"


# --- variants ---------------------------------------------------------------------------------


def test_variants_other_script_and_diacritics() -> None:
    assert variants("фрукт", "cyrillic") == ["frukt"]
    assert variants("traktor", "latin") == ["трактор"]
    assert variants("ekran", "latin") == ["экран"]  # "e" at the start is "э"
    assert variants("tomek", "latin") == ["томек"]
    assert variants("odmów", "latin") == ["odmow"]
    yo = variants("ёлк", "cyrillic")
    assert yo == ["elk", "елк"]
    assert len(yo) <= 3


def test_variants_never_return_the_prefix_or_junk() -> None:
    for prefix, script in [("фрукт", "cyrillic"), ("plant", "latin"), ("żółw", "latin")]:
        result = variants(prefix, script)
        assert prefix not in result
        assert len(result) == len(set(result)) <= 3
    assert variants("żółw", "latin") == ["zolw"]
    assert variants("2024", "other") == []
    assert variants("@green", "latin") == []
    assert variants("compan", "latin") == []  # "c" has no Cyrillic letter in the table


# --- compounds --------------------------------------------------------------------------------


def test_compounds_capped_and_ordered() -> None:
    result = compounds("трактор", "cyrillic")
    assert result[:2] == ["автотрактор", "микротрактор"]
    assert len(result) <= 20
    assert result == [head + "трактор" for head in COMPOUND_HEADS_CYRILLIC][:20]
    assert compounds("plant", "latin")[:3] == ["autoplant", "microplant", "miniplant"]
    assert compounds("liz", "latin") == []
    assert compounds("2024", "other") == []
    assert compounds("@green", "latin") == []


# --- rank -------------------------------------------------------------------------------------


def test_rank_prefers_handles_then_capitalised_then_longer() -> None:
    assert [t.text for t in rank(tokenize("фруктовая компания Zielona"))] == [
        "Zielona",
        "фруктовая",
        "компания",
    ]
    assert rank(tokenize("Zielona фруктовая @greenco"))[0].text == "@greenco"
    assert rank(tokenize("фруктовая 2024"))[0].text == "2024"
    # Capitalised as the first word means nothing; the longer word wins.
    assert rank(tokenize("Zielona фруктовая"))[0].text == "фруктовая"
    # Equal length: the earlier word wins.
    assert [t.text for t in rank(tokenize("firma kozak"))] == ["firma", "kozak"]


# --- plan -------------------------------------------------------------------------------------


def test_plan_one_word() -> None:
    p = plan("фруктовая")
    assert p.steps[0] == ("exact", "фрукт")
    assert p.steps[1] == ("variant", "frukt")
    assert p.steps[2:] == (
        ("compound", "автофрукт"),
        ("compound", "микрофрукт"),
        ("compound", "минифрукт"),
        ("compound", "макрофрукт"),
    )
    assert len(p.steps) <= MAX_QUERIES
    assert len(set(_queries(p))) == len(p.steps)


def test_plan_two_words() -> None:
    p = plan("фруктовая компания")
    assert p.steps[:3] == (
        ("exact", "фрукт компан"),
        ("single", "фрукт"),
        ("variant", "frukt"),
    )
    assert "pair" not in _labels(p)
    assert _labels(p)[3:] == ["compound"] * 3
    assert len(p.steps) == MAX_QUERIES


def test_plan_three_words_has_a_pair() -> None:
    p = plan("фруктовая компания Zielona")
    assert p.steps[:3] == (
        ("exact", "фрукт компан zielon"),
        ("pair", "фрукт zielon"),
        ("single", "zielon"),
    )
    assert p.steps[3] == ("variant", "зиелон")


def test_plan_four_words_budget() -> None:
    query = "фруктовая компания Zielona odmówiła"
    p = plan(query)
    assert len(p.steps) <= MAX_QUERIES
    g = plan(query, budget=MAX_QUERIES_GLOBAL)
    assert len(g.steps) == MAX_QUERIES_GLOBAL
    assert g.steps == p.steps[:MAX_QUERIES_GLOBAL]
    assert _labels(g) == ["exact", "pair", "single", "variant"]


def test_plan_handle_has_no_variants_or_compounds() -> None:
    assert plan("@green_co") == Plan((("exact", "@green_co"),))


def test_plan_stop_words_only() -> None:
    assert plan("и в") == Plan((("exact", "и в"),))
    assert plan("  и в ") == Plan((("exact", "и в"),))


def test_plan_empty_query() -> None:
    assert plan("") == Plan(())
    assert plan("   ") == Plan(())


def test_plan_budget_zero_or_one() -> None:
    assert plan("фруктовая", budget=1) == Plan((("exact", "фрукт"),))
    assert plan("фруктовая", budget=0) == Plan(())


def test_plan_is_deterministic() -> None:
    for query in ["фруктовая компания", "Zielona firma odmówiła", "ёлка", "@green_co трактор"]:
        first = plan(query)
        assert all(plan(query) == first for _ in range(3))
        assert len(set(_queries(first))) == len(first.steps)
