import asyncio

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
