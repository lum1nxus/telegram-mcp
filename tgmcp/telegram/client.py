"""Telethon clients that refuse every request not on an explicit read-only allowlist.

Every way Telethon talks to Telegram on behalf of the account goes through
`TelegramClient._call`: `client(request)`, the friendly methods (`send_message`,
`download_media`, `log_out`, ...) and the methods of returned objects (`Message.respond`,
`Message.delete`, `Dialog.send_message`, `Draft.send`, ...). Overriding `_call` therefore checks
every request, whoever builds it. The check runs before Telethon wraps the request in
`InvokeWithoutUpdatesRequest`, so the class seen here is the one the code asked for.

What does not pass `_call` is the transport itself: `MTProtoSender` sends
`InvokeWithLayerRequest(InitConnectionRequest(help.GetConfigRequest()))` once per connection,
pings and acknowledgements. None of these acts on the account, and none of them is reachable from
our code except through the private sender, which the contract test forbids.

A request missing from the list fails closed: `ReadOnlyViolation` names its class and nothing is
sent. Widening a list needs a pull request that says why the request cannot change anything.
"""

from typing import Any

from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from telethon.tl import functions

from tgmcp.config import Settings


class ReadOnlyViolation(RuntimeError):  # noqa: N818 - a refusal, named for what it guards
    """A request that is not on the client's allowlist; nothing was sent."""

    def __init__(self, request_name: str) -> None:
        self.request_name = request_name
        super().__init__(f"{request_name} is not on the read-only allowlist")


# What the MCP server may send. Each line says who sends it.
ALLOWED_REQUESTS: frozenset[type[Any]] = frozenset(
    {
        functions.users.GetUsersRequest,  # get_me (InputUserSelf) on connect and for the account
        functions.updates.GetStateRequest,  # is_user_authorized; the login hook on connect
        functions.updates.GetDifferenceRequest,  # the login hook on connect; the update loop
        functions.updates.GetChannelDifferenceRequest,  # the update loop, for a channel gap
        functions.help.GetConfigRequest,  # moving to another data centre (`_switch_dc`)
        functions.messages.GetDialogsRequest,  # iter_dialogs: list_chats, chat lookup
        functions.messages.GetHistoryRequest,  # iter_messages: get_messages, context
        functions.messages.GetRepliesRequest,  # iter_messages(reply_to=topic): forum topics
        functions.messages.GetMessagesRequest,  # iter_messages(ids=...) outside channels
        functions.channels.GetMessagesRequest,  # iter_messages(ids=...) in channels
        functions.messages.SearchRequest,  # search in one chat
        functions.messages.SearchGlobalRequest,  # search in all chats
        functions.channels.GetFullChannelRequest,  # get_chat: members, discussion group
        functions.messages.GetForumTopicsRequest,  # list_topics
    }
)

# What `tgmcp login` and `tgmcp logout` may send: connecting, the console sign-in and the
# log-out that ends the stored login.
AUTH_REQUESTS: frozenset[type[Any]] = frozenset(
    {
        functions.users.GetUsersRequest,  # get_me: is there a login already, who is it
        functions.updates.GetStateRequest,  # the login hook after sign-in
        functions.updates.GetDifferenceRequest,  # the login hook after sign-in
        functions.updates.GetChannelDifferenceRequest,  # the update loop, for a channel gap
        functions.help.GetConfigRequest,  # the phone lives on another data centre
        functions.auth.SendCodeRequest,  # asks Telegram to send the login code
        functions.auth.ResendCodeRequest,  # Telethon's resend when a code was already asked
        functions.auth.SignInRequest,  # the code the user typed
        functions.account.GetPasswordRequest,  # two-step verification: the password parameters
        functions.auth.CheckPasswordRequest,  # two-step verification: the password proof
        functions.auth.LogOutRequest,  # `tgmcp logout` ends the stored login
    }
)


# Methods a request object must take from its class: an instance-level `_bytes` would put other
# bytes on the wire, an instance-level `resolve` could change the object after the check.
_CLASS_ONLY = frozenset({"_bytes", "__bytes__", "resolve", "to_dict"})


def _check_one(allowed: frozenset[type[Any]], item: Any) -> None:
    name = type(item).__name__
    if type(item) not in allowed:
        raise ReadOnlyViolation(name)
    if not _CLASS_ONLY.isdisjoint(vars(item)):  # the one `vars` the contract test allows
        raise ReadOnlyViolation(f"{name} (with its own serialisation)")


def _items(request: Any) -> tuple[list[Any], bool]:
    if utils.is_list_like(request):
        return list(request), True
    return [request], False


class _AllowlistClient(TelegramClient):  # type: ignore[misc]  # Telethon has no type hints
    """Refuses every request whose exact class is not on the list of its own class.

    The list comes from `_LISTS` by the exact class of the client, never from an attribute an
    instance could replace; a subclass that is not in `_LISTS` may send nothing. The request
    is checked, resolved, and checked again, so a `resolve` that changes the object is caught.
    """

    async def _call(
        self,
        sender: Any,
        request: Any,
        ordered: bool = False,
        flood_sleep_threshold: int | None = None,
    ) -> Any:
        allowed = _LISTS.get(type(self), frozenset())
        items, batch = _items(request)
        for item in items:
            _check_one(allowed, item)
            await item.resolve(self, utils)
            _check_one(allowed, item)
        return await super()._call(
            sender,
            items if batch else items[0],
            ordered=ordered,
            flood_sleep_threshold=flood_sleep_threshold,
        )


class ReadOnlyClient(_AllowlistClient):
    """The client the MCP server uses."""


class AuthClient(_AllowlistClient):
    """The client `tgmcp login` and `tgmcp logout` use."""


_LISTS: dict[type[Any], frozenset[type[Any]]] = {
    ReadOnlyClient: ALLOWED_REQUESTS,
    AuthClient: AUTH_REQUESTS,
}


def make_client(settings: Settings, session: str, api_hash: str) -> ReadOnlyClient:
    return ReadOnlyClient(
        StringSession(session),
        settings.api_id,
        api_hash,
        flood_sleep_threshold=10,
        receive_updates=False,  # the server only asks; it never listens for updates
    )
