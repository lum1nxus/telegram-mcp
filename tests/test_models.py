from datetime import UTC, datetime

from tests.factories import make_message
from tgmcp import models


def test_cap_text_cuts_at_2000():
    text, cut = models.cap_text("x" * 4096)
    assert (len(text), cut) == (2000, True)
    assert models.cap_text("short") == ("short", False)


def test_fit_budget_ends_early_and_keeps_one():
    big = [make_message(i, text="y" * 1500) for i in range(100)]
    kept, truncated = models.fit_budget(big)
    assert truncated and 0 < len(kept) < 100
    assert models.fit_budget([big[0]], budget=10) == ([big[0]], True)


def test_fit_budget_keeps_everything_that_fits():
    items = [make_message(i) for i in range(3)]
    assert models.fit_budget(items) == (items, False)


def test_message_dates_keep_timezone():
    m = make_message(1, date=datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    assert m.model_dump(mode="json")["date"] == "2026-10-21T12:00:00Z"
