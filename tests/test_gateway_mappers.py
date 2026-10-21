import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from telethon import errors, utils
from telethon.tl import types
from telethon.tl.custom import Dialog

from tgmcp.gateway import (
    Busy,
    GatewayError,
    IdleGuard,
    NotLoggedIn,
    chat_from_dialog,
    map_error,
    message_from_telethon,
    topic_from_forum_topic,
)

WHEN = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _channel(cid: int = 1005, **kw: object) -> types.Channel:
    title = str(kw.pop("title", "Expats Hub"))
    return types.Channel(id=cid, title=title, photo=None, date=WHEN, access_hash=9, **kw)


def _dialog(entity: object, peer: object, *, folder_id: int | None = None) -> Dialog:
    raw = types.Dialog(
        peer=peer,
        top_message=1,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=0,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        notify_settings=types.PeerNotifySettings(),
        folder_id=folder_id,
    )
    last = types.Message(id=1, peer_id=peer, date=WHEN, message="x")
    return Dialog(None, raw, {utils.get_peer_id(peer): entity}, last)


def _msg(**kw: object) -> types.Message:
    fields: dict[str, object] = {"id": 5, "peer_id": types.PeerChannel(1005), "date": WHEN}
    fields.update(kw)
    return types.Message(**fields)


def _voice() -> types.MessageMediaDocument:
    doc = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=WHEN,
        mime_type="audio/ogg",
        size=10,
        dc_id=2,
        attributes=[types.DocumentAttributeAudio(duration=3, voice=True)],
    )
    return types.MessageMediaDocument(document=doc)


def test_dialog_of_forum_supergroup():
    entity = _channel(megagroup=True, forum=True, username="expats", participants_count=40)
    chat = chat_from_dialog(_dialog(entity, types.PeerChannel(1005), folder_id=1))
    assert chat.id == -1000000001005
    assert (chat.kind, chat.is_forum, chat.username, chat.members) == ("group", True, "expats", 40)
    assert chat.archived is True
    assert chat.last_message_at == WHEN
    assert chat.title == "Expats Hub"


def test_dialog_of_broadcast_channel_and_user():
    channel = chat_from_dialog(_dialog(_channel(broadcast=True), types.PeerChannel(1005)))
    assert channel.kind == "channel"
    assert channel.is_forum is False
    assert channel.archived is False
    user = types.User(id=111, first_name="Anna", username="anna", access_hash=9)
    chat = chat_from_dialog(_dialog(user, types.PeerUser(111)))
    assert (chat.id, chat.kind, chat.title, chat.username) == (111, "user", "Anna", "anna")


def test_message_in_forum_topic():
    m = _msg(message="hi", reply_to=types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=7))
    out = message_from_telethon(m, -1001005, is_forum=True, public_username="expats")
    assert out.topic_id == 7
    assert out.text == "hi"
    assert out.chat_id == -1001005
    assert out.link == "https://t.me/expats/5"
    assert out.reply_to_id == 7
    assert out.has_media is False
    assert out.media_kind is None


def test_reply_inside_topic_uses_top_id():
    header = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=9, reply_to_top_id=7)
    out = message_from_telethon(_msg(reply_to=header), -1, is_forum=True, public_username=None)
    assert (out.topic_id, out.reply_to_id, out.link) == (7, 9, None)


def test_forum_message_without_reply_is_general_topic():
    out = message_from_telethon(_msg(message="x"), -1, is_forum=True, public_username=None)
    assert out.topic_id == 1


def test_plain_group_message_has_no_topic():
    header = types.MessageReplyHeader(reply_to_msg_id=3)
    out = message_from_telethon(_msg(reply_to=header), -1, is_forum=False, public_username=None)
    assert (out.topic_id, out.reply_to_id) == (None, 3)


def test_empty_text_and_sender_id():
    m = _msg(message=None, from_id=types.PeerUser(333))
    out = message_from_telethon(m, -1, is_forum=False, public_username=None)
    assert out.text == ""
    assert out.sender_id == 333
    assert out.sender_name is None


def test_voice_media_kind():
    out = message_from_telethon(_msg(media=_voice()), -1, is_forum=False, public_username=None)
    assert out.has_media is True
    assert out.media_kind == "voice"


def test_forward_from_hidden_name():
    fwd = types.MessageFwdHeader(date=WHEN, from_name="Hidden Person")
    out = message_from_telethon(_msg(fwd_from=fwd), -1, is_forum=False, public_username=None)
    assert out.forwarded_from == "Hidden Person"


def test_forum_topic():
    t = types.ForumTopic(
        id=7,
        date=WHEN,
        peer=types.PeerChannel(1005),
        title="Cars",
        icon_color=0,
        top_message=10,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=0,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        from_id=types.PeerUser(1),
        notify_settings=types.PeerNotifySettings(),
    )
    topic = topic_from_forum_topic(t)
    assert (topic.id, topic.title, topic.last_message_at) == (7, "Cars", None)
    assert topic_from_forum_topic(t, last_message_at=WHEN).last_message_at == WHEN


def test_flood_wait_is_busy():
    error = map_error(errors.FloodWaitError(request=None, capture=42))
    assert isinstance(error, Busy)
    assert error.seconds == 42


def test_dead_session_is_not_logged_in():
    for exc in (
        errors.AuthKeyUnregisteredError(request=None),
        errors.AuthKeyDuplicatedError(request=None),
        errors.UserDeactivatedError(request=None),
    ):
        error = map_error(exc)
        assert isinstance(error, NotLoggedIn)
        assert "tgmcp login" in str(error)


def test_other_errors_are_sentences():
    private = map_error(errors.ChannelPrivateError(request=None))
    assert str(private) == "That chat is no longer accessible to this account."
    empty = map_error(errors.SearchQueryEmptyError(request=None))
    assert str(empty) == "Global search needs a query."
    rpc = map_error(errors.RPCError(request=None, message="WHATEVER", code=400))
    assert str(rpc) == "Telegram refused the request: RPCError."
    down = map_error(ConnectionError("down"))
    assert str(down) == "Could not talk to Telegram: ConnectionError."
    unknown = map_error(ValueError("Cannot find any entity corresponding to x"))
    assert str(unknown) == "Unknown sender: pass a user id or @username from this chat."
    bug = map_error(KeyError("k"))
    assert str(bug) == "Internal error in the Telegram gateway: KeyError."
    same = GatewayError("kept")
    assert map_error(same) is same


async def test_idle_guard_fires_once_after_idle():
    now = [0.0]
    calls: list[float] = []

    async def on_idle() -> None:
        calls.append(now[0])

    guard = IdleGuard(0.05, on_idle, clock=lambda: now[0])
    task = asyncio.create_task(guard.run())
    await asyncio.sleep(0.02)
    now[0] = 0.04
    guard.touch()
    now[0] = 0.08
    await asyncio.sleep(0.02)
    assert calls == []
    now[0] = 0.2
    await asyncio.wait_for(task, 1)
    assert calls == [0.2]


async def test_idle_guard_stop_ends_without_firing():
    calls: list[int] = []

    async def on_idle() -> None:
        calls.append(1)

    guard = IdleGuard(0.05, on_idle, clock=lambda: 0.0)
    task = asyncio.create_task(guard.run())
    guard.stop()
    await asyncio.wait_for(task, 1)
    assert calls == []


def test_sender_name_from_known_entity():
    client = SimpleNamespace(_self_id=1, _mb_entity_cache=SimpleNamespace(get=lambda _id: None))
    ola = types.User(id=333, first_name="Ola", last_name="K", access_hash=9)
    m = _msg(message="x", from_id=types.PeerUser(333))
    m._finish_init(client, {333: ola}, None)
    out = message_from_telethon(m, -1, is_forum=False, public_username=None)
    assert (out.sender_id, out.sender_name) == (333, "Ola K")


def test_video_sticker_is_a_sticker():
    doc = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=WHEN,
        mime_type="video/webm",
        size=10,
        dc_id=2,
        attributes=[
            types.DocumentAttributeVideo(duration=1, w=512, h=512),
            types.DocumentAttributeSticker(alt="x", stickerset=types.InputStickerSetEmpty()),
        ],
    )
    media = types.MessageMediaDocument(document=doc)
    out = message_from_telethon(_msg(media=media), -1, is_forum=False, public_username=None)
    assert out.media_kind == "sticker"
