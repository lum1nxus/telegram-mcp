from datetime import UTC, datetime, timedelta

import pytest

from tests.factories import make_chat, make_message, make_topic
from tests.fake_gateway import FakeGateway
from tgmcp.models import Message

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _hiking_messages() -> list[Message]:
    out: list[Message] = []
    for n in range(1, 121):
        fields: dict[str, object] = {
            "chat_id": -1001,
            "date": _NOW - timedelta(hours=120 - n),
            "text": f"message {n}",
        }
        if n % 10:
            fields.update(sender_id=333, sender_name="Ola")
        if n == 50:
            fields["text"] = "Granite peak route was easy"
        if n == 60:
            fields["text"] = "Ridge trail closed"
        if n == 120:
            fields["text"] = "x" * 4096
        out.append(make_message(n, **fields))
    return out


@pytest.fixture
def seed() -> FakeGateway:
    chats = [
        make_chat(111, title="Anna", kind="user", username="anna"),
        make_chat(-1001, title="Hiking Club", members=3),
        make_chat(-1002, title="Hiking Crew"),
        make_chat(
            -1003,
            title="Auto News",
            kind="channel",
            username="autonews",
            discussion_chat_id=-1004,
        ),
        make_chat(-1004, title="Auto News chat"),
        make_chat(-1005, title="Expats Hub", is_forum=True),
        make_chat(-1006, title="Old", archived=True),
        make_chat(-1007, title="Private"),
    ]
    forum = [
        make_message(
            n, chat_id=-1005, topic_id=1 if n <= 5 else 7, date=_NOW - timedelta(hours=10 - n)
        )
        for n in range(1, 11)
    ]
    return FakeGateway(
        chats,
        {-1001: _hiking_messages(), -1005: forum},
        {-1005: [make_topic(1, title="General"), make_topic(7, title="Cars")]},
    )
