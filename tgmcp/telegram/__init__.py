"""The Telethon side: the only package that imports Telethon.

`client` holds the allowlisting clients, `live` the gateway the MCP server uses, `auth` the
login and logout, `mappers` and `errors` the pure conversions.
"""

from tgmcp.telegram.auth import end_session, interactive_login, load_session_string
from tgmcp.telegram.client import (
    ALLOWED_REQUESTS,
    AUTH_REQUESTS,
    AuthClient,
    ReadOnlyClient,
    ReadOnlyViolation,
)
from tgmcp.telegram.live import TelethonGateway, factory

__all__ = [
    "ALLOWED_REQUESTS",
    "AUTH_REQUESTS",
    "AuthClient",
    "ReadOnlyClient",
    "ReadOnlyViolation",
    "TelethonGateway",
    "end_session",
    "factory",
    "interactive_login",
    "load_session_string",
]
