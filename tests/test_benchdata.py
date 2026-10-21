# ruff: noqa: RUF001 - Russian seed text has one-letter Cyrillic words
import asyncio
from datetime import timedelta

from tgmcp.host import benchdata


def test_seed_has_multilingual_chat() -> None:
    seed = benchdata.build_seed()
    chats = {c.id: c for c in asyncio.run(seed.dialogs())}
    assert chats[-1008].title == "Sąsiedzi / Соседи"
    hits = asyncio.run(
        seed.search_in_chat(
            -1008, "лифт", topic=None, sender=None, since=None, until=None, limit=10
        )
    )
    assert [m.id for m in hits] == [8]


def test_v1_seed_unchanged() -> None:
    seed = benchdata.build_seed()
    chats = {c.id: c.title for c in asyncio.run(seed.dialogs())}
    assert [chats[i] for i in range(-1001, -1008, -1)] == [
        "Hiking Club",
        "Hiking Crew",
        "Auto News",
        "Auto News chat",
        "Expats Hub",
        "Old",
        "Private",
    ]
    hiking = seed.messages[-1001]
    assert [m.id for m in hiking] == list(range(1, 121))
    assert len(hiking[-1].text) == 4096


def test_build_seed_returns_a_fresh_gateway() -> None:
    assert benchdata.build_seed() is not benchdata.build_seed()


def test_seed_garden_club() -> None:
    seed = benchdata.build_seed()
    chat = next(c for c in asyncio.run(seed.dialogs()) if c.id == -1009)
    assert (chat.title, chat.kind, chat.members) == ("Garden Club", "group", 4)
    messages = sorted(seed.messages[-1009], key=lambda m: m.id)
    assert [m.text for m in messages] == [
        "взяли минитрактор у Tomka",
        "kosiarka bez problemu",
        "Zielona firma odmówiła",
        "компания GreenCo ответила через неделю",
        "Tomek поделился теплицей с соседями",
    ]
    assert {(m.sender_id, m.sender_name) for m in messages} == {(555, "Dana")}
    hours = [(benchdata.NOW - m.date) / timedelta(hours=1) for m in messages]
    assert hours == [5, 4, 3, 2, 1]  # hourly before NOW; the higher id is the newer message


def _search(seed: benchdata.FakeGateway, query: str) -> list[int]:
    hits = asyncio.run(
        seed.search_in_chat(-1009, query, topic=None, sender=None, since=None, until=None, limit=10)
    )
    return [m.id for m in hits]


def test_fake_search_folds_diacritics_on_both_sides() -> None:
    seed = benchdata.build_seed()
    assert _search(seed, "odmowila") == [3]
    assert _search(seed, "odmówiła") == [3]
    assert _search(seed, "ODMOW") == [3]
    assert _search(seed, "zielóna") == [3]
    assert _search(seed, "компания") == [4]
