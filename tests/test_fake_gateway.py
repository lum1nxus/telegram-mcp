import pytest

from tgmcp.gateway import GatewayError


async def test_search_matches_word_prefixes_not_fragments(seed):
    hits = await seed.search_in_chat(
        -1001, "liz", topic=None, sender=None, since=None, until=None, limit=10
    )
    assert [m.id for m in hits] == [50]
    none = await seed.search_in_chat(
        -1001, "ranite", topic=None, sender=None, since=None, until=None, limit=10
    )
    assert none == []


async def test_search_needs_every_word(seed):
    both = await seed.search_global("granite pea", since=None, until=None, limit=10)
    assert [m.id for m in both] == [50]
    mixed = await seed.search_global("granite ridge", since=None, until=None, limit=10)
    assert mixed == []


async def test_history_before_id_is_newest_first(seed):
    page = await seed.history(-1001, topic=None, before_id=10, since=None, until=None, limit=3)
    assert [m.id for m in page] == [9, 8, 7]


async def test_around_is_chronological_and_missing_id_raises(seed):
    before, target, after = await seed.around(-1001, 50, before=2, after=2)
    assert [m.id for m in before] == [48, 49]
    assert target.id == 50
    assert [m.id for m in after] == [51, 52]
    with pytest.raises(GatewayError, match="No message 999"):
        await seed.around(-1001, 999, before=1, after=1)


async def test_raise_next_fires_once(seed):
    seed.raise_next = GatewayError("boom")
    with pytest.raises(GatewayError):
        await seed.me()
    assert (await seed.me()).id == 100
