import pytest

from tgmcp.gateway import UNSEEN_USER, GatewayError


async def test_search_matches_word_prefixes_not_fragments(seed):
    hits = await seed.search_in_chat(
        -1001, "gran", topic=None, sender=None, since=None, until=None, limit=10
    )
    assert [m.id for m in hits] == [50]
    none = await seed.search_in_chat(
        -1001, "ranite", topic=None, sender=None, since=None, until=None, limit=10
    )
    assert none == []


async def test_search_needs_every_word(seed):
    both = await seed.search_global("granite pea", since=None, until=None, limit=10)
    assert [m.id for m in both.messages] == [50]
    mixed = await seed.search_global("granite ridge", since=None, until=None, limit=10)
    assert mixed.messages == []


async def test_history_before_id_is_newest_first(seed):
    page = await seed.history(-1001, topic=None, before_id=10, since=None, until=None, limit=3)
    assert [m.id for m in page.messages] == [9, 8, 7]
    assert page.next_before_id == 7


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


async def test_sender_username_maps_to_user_id(seed):
    seed.messages[-1001][0] = seed.messages[-1001][0].model_copy(update={"sender_id": 111})
    hits = await seed.search_in_chat(
        -1001, "", topic=None, sender="@nora", since=None, until=None, limit=5
    )
    assert [m.id for m in hits] == [1]
    with pytest.raises(GatewayError, match="Unknown sender"):
        await seed.search_in_chat(
            -1001, "", topic=None, sender="@x", since=None, until=None, limit=5
        )


async def test_empty_query_returns_everything_newest_first(seed):
    hits = await seed.search_in_chat(
        -1001, "", topic=None, sender=None, since=None, until=None, limit=3
    )
    assert [m.id for m in hits] == [120, 119, 118]


async def test_sender_must_be_a_dialog_user(seed):
    with pytest.raises(GatewayError) as caught:
        await seed.search_in_chat(
            -1001, "", topic=None, sender="333", since=None, until=None, limit=5
        )
    assert str(caught.value) == UNSEEN_USER  # Ben writes in the group but is no dialog
    seed.messages[-1001][0] = seed.messages[-1001][0].model_copy(update={"sender_id": 111})
    hits = await seed.search_in_chat(
        -1001, "", topic=None, sender="111", since=None, until=None, limit=2
    )
    assert [m.id for m in hits] == [1]


async def test_history_until_is_exclusive(seed):
    newest = max(seed.messages[-1001], key=lambda m: m.id)
    page = await seed.history(
        -1001, topic=None, before_id=None, since=None, until=newest.date, limit=1
    )
    assert [m.id for m in page.messages] == [newest.id - 1]


async def test_service_messages_are_skipped_and_stop_a_page_early(seed):
    seed.service[-1001] = set(range(100, 120))
    page = await seed.history(-1001, topic=None, before_id=None, since=None, until=None, limit=2)
    assert [m.id for m in page.messages] == [120]
    assert (page.next_before_id, page.stopped_early) == (113, True)
    with pytest.raises(GatewayError, match="No message 110"):
        await seed.around(-1001, 110, before=1, after=1)
    before, _, after = await seed.around(-1001, 99, before=2, after=2)
    assert ([m.id for m in before], after) == ([97, 98], [])


async def test_forum_context_stays_in_the_topic(seed):
    before, target, after = await seed.around(-1005, 6, before=3, after=3)
    assert target.topic_id == 7
    assert {m.topic_id for m in before + after} == {7}
    assert [m.id for m in before] == []
    assert [m.id for m in after] == [7, 8, 9]
