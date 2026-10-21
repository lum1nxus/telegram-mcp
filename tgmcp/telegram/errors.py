"""Telethon errors -> one sentence for the model."""

import logging
import traceback

from telethon import errors

from tgmcp.gateway import Busy, GatewayError, NotLoggedIn
from tgmcp.telegram.client import ReadOnlyViolation

log = logging.getLogger(__name__)

# The login key is dead: revoked, used from two IP addresses at once, or the account is gone.
DEAD_SESSION: tuple[type[Exception], ...] = (
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.UserDeactivatedError,
)
_FIXED_ERRORS: tuple[tuple[tuple[type[Exception], ...], type[GatewayError], str], ...] = (
    (
        DEAD_SESSION,
        NotLoggedIn,
        "Telegram no longer accepts this session: run `tgmcp logout`, then `tgmcp login`.",
    ),
    (
        (errors.ChannelPrivateError,),
        GatewayError,
        "That chat is no longer accessible to this account.",
    ),
    ((errors.SearchQueryEmptyError,), GatewayError, "Global search needs a query."),
)


def refused(e: ReadOnlyViolation) -> GatewayError:
    log.error("read-only guard refused %s", e.request_name)
    return GatewayError(
        f"tgmcp refused {e.request_name}, a request that is not on its read-only list; "
        "nothing was sent. This is a bug in tgmcp: please report it."
    )


def map_error(e: Exception) -> GatewayError:
    """One sentence for the model; never the raw Telethon text."""
    if isinstance(e, GatewayError):
        return e
    if isinstance(e, ReadOnlyViolation | errors.FloodWaitError):
        return refused(e) if isinstance(e, ReadOnlyViolation) else Busy(seconds=int(e.seconds))
    for kinds, error_cls, sentence in _FIXED_ERRORS:
        if isinstance(e, kinds):
            return error_cls(sentence)
    if isinstance(e, errors.RPCError):
        return GatewayError(f"Telegram refused the request: {type(e).__name__}.")
    if isinstance(e, OSError):  # network errors and timeouts
        return GatewayError(f"Could not talk to Telegram: {type(e).__name__}.")
    # The stack, never the message: a pydantic ValidationError would print `input_value=`,
    # which can be chat text.
    stack = "".join(traceback.format_tb(e.__traceback__))
    log.error("internal gateway error %s\n%s", type(e).__name__, stack)
    return GatewayError(f"Internal error in the Telegram gateway: {type(e).__name__}.")
