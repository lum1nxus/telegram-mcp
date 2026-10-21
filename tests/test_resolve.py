import pytest

from tests.factories import make_chat
from tgmcp import resolve


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_resolve_by_id_username_title_and_fragment(seed):
    d = resolve.Directory(seed, hidden=[])
    assert (await d.resolve("-1001")).id == -1001
    assert (await d.resolve("@autonews")).id == -1003
    assert (await d.resolve("Hiking Club")).id == -1001
    assert (await d.resolve("poland")).id == -1002


async def test_fragment_matching_two_chats_is_ambiguous(seed):
    d = resolve.Directory(seed, hidden=[])
    with pytest.raises(resolve.Ambiguous) as e:
        await d.resolve("hiking")
    assert "Hiking Club (-1001)" in str(e.value)
    assert "Hiking Crew (-1002)" in str(e.value)
    assert [c.id for c in e.value.candidates] == [-1001, -1002]
    assert str(e.value) == (
        "Several chats match 'hiking': Hiking Club (-1001), Hiking Crew (-1002). "
        "Call again with the id."
    )


async def test_hidden_chat_is_invisible(seed):
    d = resolve.Directory(seed, hidden=["-1007"])
    with pytest.raises(resolve.NotFound) as e:
        await d.resolve("Private")
    assert "among your 7 chats" in str(e.value)
    with pytest.raises(resolve.NotFound):
        await d.resolve("-1007")
    assert -1007 not in [c.id for c in await d.chats()]


async def test_hidden_by_username_is_case_insensitive(seed):
    d = resolve.Directory(seed, hidden=["@AutoNews"])
    with pytest.raises(resolve.NotFound):
        await d.resolve("@autonews")
    assert d.is_hidden(make_chat(5, username="AUTONEWS"))
    assert not d.is_hidden(make_chat(5))


async def test_archived_chat_resolves(seed):
    d = resolve.Directory(seed, hidden=[])
    assert (await d.resolve("Old")).id == -1006


async def test_miss_refreshes_once(seed):
    clock = Clock()
    d = resolve.Directory(seed, hidden=[], refresh_after_s=60.0, clock=clock)
    await d.resolve("Anna")
    assert seed.calls.count("dialogs") == 1
    seed.chats.append(make_chat(-2000, title="Fresh Arrival"))
    clock.now = 100.0
    assert (await d.resolve("fresh arrival")).id == -2000
    assert seed.calls.count("dialogs") == 2


async def test_fresh_cache_is_not_refreshed_on_miss(seed):
    d = resolve.Directory(seed, hidden=[], clock=Clock())
    with pytest.raises(resolve.NotFound):
        await d.resolve("xyz")
    assert seed.calls.count("dialogs") == 1


async def test_hidden_by_bare_username_and_by_title(seed):
    d = resolve.Directory(seed, hidden=["autonews", "PRIVATE"])
    ids = [c.id for c in await d.chats()]
    assert -1003 not in ids
    assert -1007 not in ids
    with pytest.raises(resolve.NotFound):
        await d.resolve("@autonews")


async def test_chats_refreshes_by_age_and_returns_a_copy(seed):
    clock = Clock()
    d = resolve.Directory(seed, hidden=[], refresh_after_s=60.0, clock=clock)
    first = await d.chats()
    first.clear()
    assert len(await d.chats()) == 8
    assert seed.calls.count("dialogs") == 1
    seed.chats.append(make_chat(-2000, title="Fresh Arrival"))
    clock.now = 100.0
    assert -2000 in [c.id for c in await d.chats()]
    assert seed.calls.count("dialogs") == 2


async def test_exact_title_beats_ambiguous_substring(seed):
    d = resolve.Directory(seed, hidden=[])
    assert (await d.resolve("auto news")).id == -1003
