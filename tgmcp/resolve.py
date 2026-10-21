"""Turn what the user typed (id, @username, title or fragment) into one chat."""

import re
import time
from collections.abc import Callable, Sequence

from tgmcp.gateway import Gateway
from tgmcp.models import Chat


class Ambiguous(Exception):  # noqa: N818 - name fixed by the interface
    def __init__(self, ref: str, candidates: list[Chat]) -> None:
        self.candidates = candidates
        listed = ", ".join(f"{c.title} ({c.id})" for c in candidates)
        super().__init__(f"Several chats match {ref!r}: {listed}. Call again with the id.")


class NotFound(Exception):  # noqa: N818 - name fixed by the interface
    def __init__(self, ref: str, total: int) -> None:
        super().__init__(f"No chat matches {ref!r} among your {total} chats (archived included).")


class Directory:
    def __init__(
        self,
        gateway: Gateway,
        hidden: Sequence[str],
        refresh_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._hidden = {h.strip().lower() for h in hidden}
        self._refresh_after_s = refresh_after_s
        self._clock = clock
        self._cache: list[Chat] | None = None
        self._loaded_at = 0.0

    def is_hidden(self, chat: Chat) -> bool:
        if str(chat.id) in self._hidden:
            return True
        return chat.username is not None and f"@{chat.username}".lower() in self._hidden

    async def _load(self) -> list[Chat]:
        dialogs = await self._gateway.dialogs()
        self._cache = [c for c in dialogs if not self.is_hidden(c)]
        self._loaded_at = self._clock()
        return self._cache

    async def chats(self) -> list[Chat]:
        if self._cache is None:
            return await self._load()
        return self._cache

    async def resolve(self, ref: str) -> Chat:
        chats = await self.chats()
        found = _match(ref, chats)
        if found is None and self._clock() - self._loaded_at > self._refresh_after_s:
            chats = await self._load()
            found = _match(ref, chats)
        if found is None:
            raise NotFound(ref, len(chats))
        return found


def _exact_ref(text: str, chats: list[Chat]) -> Chat | None:
    """Match by numeric id or by @username."""
    if re.fullmatch(r"-?\d+", text):
        for chat in chats:
            if chat.id == int(text):
                return chat
    if text.startswith("@"):
        for chat in chats:
            if chat.username is not None and chat.username.lower() == text[1:].lower():
                return chat
    return None


def _match(ref: str, chats: list[Chat]) -> Chat | None:
    text = ref.strip()
    found = _exact_ref(text, chats)
    if found is not None:
        return found
    low = text.lower()
    for chat in chats:
        if chat.title.lower() == low:
            return chat
    partial = [c for c in chats if low and low in c.title.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise Ambiguous(ref, partial)
    return None
