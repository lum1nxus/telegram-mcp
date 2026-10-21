"""Telethon objects -> models. Pure functions: nothing here talks to Telegram.

`sender`, `sender_id` and `forward` are read-only Telethon properties; the contract test
allowlists exactly these attribute names.
"""

from datetime import datetime
from typing import Any

from telethon import utils
from telethon.tl import types

from tgmcp.models import Chat, Kind, MediaKind, Message, Topic


def chat_from_dialog(d: Any) -> Chat:
    entity = d.entity
    if d.is_user:
        kind: Kind = "user"
    elif isinstance(entity, types.Channel) and entity.broadcast:
        kind = "channel"
    else:
        kind = "group"
    return Chat(
        id=d.id,
        title=d.title or "",
        kind=kind,
        username=getattr(entity, "username", None),
        members=getattr(entity, "participants_count", None),
        last_message_at=d.date,
        archived=bool(d.archived),
        is_forum=bool(getattr(entity, "forum", False)),
    )


def _topic_id(m: Any, is_forum: bool) -> int | None:
    header = m.reply_to
    if header is not None and getattr(header, "forum_topic", False):
        top: int | None = header.reply_to_top_id or header.reply_to_msg_id
        return top
    return 1 if is_forum else None


def _media_kind(m: Any) -> MediaKind | None:
    if m.media is None or isinstance(m.media, types.MessageMediaWebPage):
        return None
    # Sticker before video: a video sticker is both.
    checks: tuple[tuple[Any, MediaKind], ...] = (
        (m.photo, "photo"),
        (m.voice, "voice"),
        (m.sticker, "sticker"),
        (m.video or m.video_note or m.gif, "video"),
        (m.document, "document"),
    )
    return next((kind for present, kind in checks if present), "other")


def display_name(entity: Any) -> str | None:
    return utils.get_display_name(entity) or None if entity is not None else None


def _fwd_origin(m: Any) -> str | None:
    if m.fwd_from is None:
        return None
    if m.fwd_from.from_name:
        return str(m.fwd_from.from_name)
    fwd = m.forward
    if fwd is None:
        return None
    return display_name(fwd.sender) or display_name(fwd.chat)


def message_from_telethon(
    m: Any, chat_id: int, *, is_forum: bool, public_username: str | None
) -> Message:
    kind = _media_kind(m)
    return Message(
        id=m.id,
        chat_id=chat_id,
        topic_id=_topic_id(m, is_forum),
        date=m.date,
        sender_id=m.sender_id,
        sender_name=display_name(m.sender),
        text=m.raw_text or "",
        reply_to_id=m.reply_to_msg_id,
        has_media=kind is not None,
        media_kind=kind,
        forwarded_from=_fwd_origin(m),
        link=f"https://t.me/{public_username}/{m.id}" if public_username else None,
    )


def topic_from_forum_topic(t: Any, *, last_message_at: datetime | None = None) -> Topic:
    """`t` is a `ForumTopic`; callers skip `ForumTopicDeleted`. `t.date` is the creation date,
    so the date of the topic's last message comes from the caller."""
    return Topic(id=t.id, title=t.title, last_message_at=last_message_at)


def is_plain(m: Any) -> bool:
    """True for a real message: not None (a missing id), not a service message (topic created,
    member joined) and not an empty placeholder. Service messages carry no text."""
    return isinstance(m, types.Message)
