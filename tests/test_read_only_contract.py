"""The read-only contract, enforced on the source and at run time.

Static (the AST of every module under `tgmcp/`):
  (a) no name, attribute, import, definition or identifier-like string anywhere in the package
      contains a write-shaped word (a denylist, case-insensitive, underscores ignored);
  (b) inside `tgmcp/telegram/`, every Telethon name used (imports, `functions.<ns>.<Request>`,
      attributes of the client, of messages and of dialogs) is on `ALLOWED_TELETHON_API`, and
      every other attribute is the package's own or on the short `PYTHON_ATTRIBUTES` list;
  (c) no dynamic lookup anywhere in the package (`eval`, `exec`, `compile`, `importlib`,
      `operator`, `vars`, `__dict__`, `alltlobjects`, computed `getattr`, ...).
Run time: `ReadOnlyClient._call` refuses every request that is not on `ALLOWED_REQUESTS`,
whoever builds it (the client, a returned `Message`, a list of requests).

A pull request that widens `ALLOWED_TELETHON_API`, `ALLOWED_NAMES` or the request allowlists in
`tgmcp/telegram/client.py` must say why the name cannot change anything (CONTRIBUTING.md).
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.tl import types
from telethon.tl.custom import Dialog
from telethon.tl.custom import Message as TelethonMessage

from tgmcp import gateway
from tgmcp.config import Settings
from tgmcp.telegram.client import (
    ALLOWED_REQUESTS,
    AUTH_REQUESTS,
    AuthClient,
    ReadOnlyClient,
    ReadOnlyViolation,
)
from tgmcp.telegram.live import TelethonGateway

PACKAGE = Path(__file__).resolve().parent.parent / "tgmcp"
TELEGRAM = PACKAGE / "telegram"

# (a) Write-shaped words. A name that contains one is refused unless it is in ALLOWED_NAMES.
FORBIDDEN = (
    "send",
    "edit",
    "delete",
    "forward",
    "mark",
    "read_history",
    "react",
    "pin",
    "join",
    "leave",
    "invite",
    "upload",
    "action",  # client.action(chat, "typing") sends a chat action
    "respond",  # Message.respond sends a message
    "reply",  # Message.reply sends a message
    "click",  # Message.click presses a button (a bot callback)
    "kick",
    "download",  # writes media to disk; the README promises no download
    "block",
    "report",
    "takeout",
    "inline",  # inline_query talks to a bot
    "draft",
    "toggle",
    "create",
    "sign_up",
    "qr_login",
)
# Read-only names that contain a forbidden word: Telethon properties read as attributes, the
# model fields that hold them, `sender` (the Protocol's keyword), words of the standard library
# (`typing`, `Mapping`, `str.join`, `set.isdisjoint`, `asyncio.create_task`, ctypes' buffer)
# and the login requests that only `tgmcp login` may send (AUTH_REQUESTS).
ALLOWED_NAMES = frozenset(
    {
        "typing",
        "Mapping",
        "isdisjoint",
        "sender_name",
        "reply_to_id",
        "forwarded_from",
        "sender",
        "sender_id",
        "forward",
        "fwd_from",
        "reply_to",
        "reply_to_msg_id",
        "reply_to_top_id",
        "GetRepliesRequest",
        "join",
        "create_task",
        "create_string_buffer",
        "SendCodeRequest",
        "ResendCodeRequest",
    }
)

# (b) Every Telethon name the telegram package may touch.
ALLOWED_TELETHON_API = frozenset(
    {
        # modules and imported names
        "telethon",
        "telethon.sessions",
        "telethon.tl",
        "TelegramClient",
        "StringSession",
        "SQLiteSession",
        "errors",
        "functions",
        "types",
        "utils",
        # request namespaces and requests (the runtime allowlists in client.py)
        "account",
        "auth",
        "channels",
        "help",
        "messages",
        "updates",
        "users",
        *(r.__name__ for r in ALLOWED_REQUESTS | AUTH_REQUESTS),
        # the client: connecting, the iterators, the console login, `_call` (overridden)
        "_call",
        "connect",
        "disconnect",
        "is_connected",
        "is_user_authorized",
        "get_me",
        "get_input_entity",
        "iter_dialogs",
        "iter_messages",
        "start",
        "log_out",
        "session",
        "save",
        "close",
        "auth_key",
        # utils
        "get_display_name",
        "get_peer_id",
        "is_list_like",
        # types read or built
        "Channel",
        "ForumTopic",
        "InputMessagesFilterEmpty",
        "InputPeerChannel",
        "InputPeerUser",
        "Message",
        "MessageMediaWebPage",
        "PeerChannel",
        # dialog attributes
        "entity",
        "input_entity",
        "is_user",
        "title",
        "date",
        "archived",
        "id",
        # message attributes (all read-only properties or fields)
        "chat",
        "chat_id",
        "sender",
        "sender_id",
        "raw_text",
        "reply_to",
        "reply_to_msg_id",
        "reply_to_top_id",
        "forum_topic",
        "media",
        "photo",
        "voice",
        "sticker",
        "video",
        "video_note",
        "gif",
        "document",
        "fwd_from",
        "from_name",
        "forward",
        "_finish_init",  # attaches a raw search result's users and chats, as iter_messages does
        # entity and result fields
        "broadcast",
        "username",
        "first_name",
        "full_chat",
        "linked_chat_id",
        "participants_count",
        "top_message",
        "topics",
        "chats",
        "seconds",
        "request_name",
        # errors caught or mapped
        "ApiIdInvalidError",
        "AuthKeyDuplicatedError",
        "AuthKeyUnregisteredError",
        "ChannelPrivateError",
        "FloodWaitError",
        "PasswordHashInvalidError",
        "PhoneCodeExpiredError",
        "PhoneCodeInvalidError",
        "PhoneNumberInvalidError",
        "PhoneNumberUnoccupiedError",
        "RPCError",
        "SearchQueryEmptyError",
        "SessionPasswordNeededError",
        "UserDeactivatedError",
    }
)
# Attributes of the standard library and pydantic used in the telegram package.
PYTHON_ATTRIBUTES = frozenset(
    {
        "Lock",
        "Task",
        "add_done_callback",
        "append",
        "astimezone",
        "cancel",
        "cancelled",
        "current_task",
        "error",
        "exception",
        "exists",
        "fullmatch",
        "get",
        "getLogger",
        "info",
        "legacy_session_path",
        "lower",
        "lstrip",
        "model_copy",
        "name",
        "secrets_path",
        "stop",
        "touch",
        "unlink",
        "warning",
        "with_name",
        "Error",  # sqlite3.Error
        "CalledProcessError",
        "create_task",
        "format_tb",
        "isdisjoint",
        "join",
        "__traceback__",
        "__init__",
        "__name__",
    }
)
# The Telethon surface (b) checks against: client, message and dialog attributes.
TELETHON_SURFACE = frozenset(
    n for n in {*dir(TelegramClient), *dir(TelethonMessage), *dir(Dialog)} if n[:2] != "__"
)

# (c) Dynamic lookups that would walk around (a) and (b).
DYNAMIC_NAMES = frozenset(
    {"eval", "exec", "compile", "__import__", "vars", "globals", "locals", "breakpoint"}
)
DYNAMIC_ATTRIBUTES = frozenset(
    {
        "__dict__",
        "__mro__",
        "__class__",
        "getmembers",
        "__subclasses__",
        "__getattribute__",
        "__globals__",
        "__builtins__",
        "__bases__",
        "attrgetter",
        "methodcaller",
        "import_module",
    }
)
DYNAMIC_MODULES = ("importlib", "operator", "alltlobjects", "tlobjects", "builtins", "inspect")
# The one dynamic name the package needs: the guard reads a request's own attributes with
# `vars` to refuse an instance-level `_bytes` or `resolve`.
DYNAMIC_EXEMPTIONS = {"telegram/client.py": frozenset({"vars"})}
# Attributes nothing may assign or delete: the guard's lists and hook, what a request sends,
# and (with TELETHON_SURFACE) every attribute of a Telethon client, message or dialog.
GUARDED_ATTRIBUTES = frozenset(
    {
        "_call",
        "_LISTS",
        "allowed",
        "ALLOWED_REQUESTS",
        "AUTH_REQUESTS",
        "_bytes",
        "__bytes__",
        "resolve",
        "to_dict",
        "__class__",
        "_sender",
        "CONSTRUCTOR_ID",
    }
)
CLIENT_CLASSES = frozenset({"TelegramClient", "_AllowlistClient", "ReadOnlyClient", "AuthClient"})
# Where a class may extend a client: the guard's own module, and only these two bases.
SUBCLASS_EXEMPTIONS = {"telegram/client.py": frozenset({"TelegramClient", "_AllowlistClient"})}
NAME_LOOKUPS = frozenset({"getattr", "setattr", "delattr", "hasattr"})
MANGLED = re.compile(r"_[A-Za-z]\w*__\w+")


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


_WORDS = tuple(_norm(w) for w in FORBIDDEN)


def is_forbidden(name: str) -> bool:
    """Case-insensitive, underscores ignored: `ReadHistoryRequest` matches `read_history`."""
    if name in ALLOWED_NAMES:
        return False
    normal = _norm(name)
    return any(w in normal for w in _WORDS)


# --- (a) the denylist ------------------------------------------------------------------------


def _defined(node: ast.AST) -> str | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    if isinstance(node, ast.arg):
        return node.arg
    return None


def _string_parts(value: str) -> list[str]:
    return [part.strip() for part in value.split(".") if part.strip().isidentifier()]


def _denylist(node: ast.AST) -> list[str]:
    line = getattr(node, "lineno", 0)
    names: list[str] = []
    if isinstance(node, ast.Attribute):
        names = [node.attr]
    elif isinstance(node, ast.Name):
        names = [node.id]
    elif isinstance(node, ast.Import | ast.ImportFrom):
        module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
        names = [*module.split("."), *(p for a in node.names for p in a.name.split("."))]
        names += [a.asname for a in node.names if a.asname]
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        names = _string_parts(node.value)
    elif (defined := _defined(node)) is not None:
        names = [defined]
    return [f"line {line}: {n}" for n in names if is_forbidden(n)]


# --- (c) dynamic lookups ---------------------------------------------------------------------


def _computed_lookup(node: ast.Call) -> bool:
    if not (isinstance(node.func, ast.Name) and node.func.id in NAME_LOOKUPS):
        return False
    name = node.args[1] if len(node.args) > 1 else None
    return not (isinstance(name, ast.Constant) and isinstance(name.value, str))


def _dynamic(node: ast.AST) -> list[str]:  # noqa: PLR0911 - one return per kind of node
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Name) and node.id in DYNAMIC_NAMES:
        return [f"line {line}: dynamic {node.id}"]
    if isinstance(node, ast.Attribute) and (
        node.attr in DYNAMIC_ATTRIBUTES or MANGLED.fullmatch(node.attr)
    ):
        return [f"line {line}: dynamic attribute {node.attr}"]
    if isinstance(node, ast.Import | ast.ImportFrom):
        modules = [a.name for a in node.names]
        if isinstance(node, ast.ImportFrom):
            modules = [node.module or "", *(f"{node.module}.{a.name}" for a in node.names)]
        if "*" in [a.name for a in node.names]:
            return [f"line {line}: star import"]
        bad = [m for m in modules if any(d in m.split(".") for d in DYNAMIC_MODULES)]
        return [f"line {line}: import {m}" for m in bad]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        found = [d for d in (*DYNAMIC_MODULES[2:], *DYNAMIC_ATTRIBUTES) if d in node.value]
        return [f"line {line}: string names {d}" for d in found]
    if isinstance(node, ast.Call) and _computed_lookup(node):
        return [f"line {line}: attribute lookup with a computed name"]
    return []


# --- (b) the Telethon allowlist --------------------------------------------------------------


def _own_names(trees: list[ast.AST]) -> frozenset[str]:
    """Names the package defines itself: functions, classes, assigned attributes and fields."""
    own: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if (defined := _defined(node)) is not None:
                own.add(defined)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                own.add(node.attr)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                own.add(node.target.id)
            elif isinstance(node, ast.Assign):
                own.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return frozenset(own)


def _chain(node: ast.Attribute) -> list[str]:
    parts = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return parts[::-1]


def _telethon(node: ast.AST, own: frozenset[str]) -> list[str]:
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("telethon"):
        names = [node.module or "", *(a.name for a in node.names)]
        return [f"line {line}: telethon name {n}" for n in names if n not in ALLOWED_TELETHON_API]
    if not isinstance(node, ast.Attribute):
        return []
    chain = _chain(node)
    if chain[0] == "functions" or node.attr in TELETHON_SURFACE:
        known = node.attr in ALLOWED_TELETHON_API
    else:
        known = node.attr in ALLOWED_TELETHON_API | PYTHON_ATTRIBUTES | own
    return [] if known else [f"line {line}: telethon name {'.'.join(chain)}"]


# --- the checker -----------------------------------------------------------------------------


def _base_names(node: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(".".join(_chain(base)))
    return names


def _tampering(node: ast.AST, module: str) -> list[str]:
    """Assigning the guard's attributes, calling `_call` past it, subclassing a client or a
    request: the ways to make the runtime guard check one thing and send another."""
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Attribute):
        if isinstance(node.ctx, ast.Store | ast.Del) and (
            node.attr in GUARDED_ATTRIBUTES or node.attr in TELETHON_SURFACE
        ):
            return [f"line {line}: assigns {node.attr}"]
        through_super = (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "super"
        )
        if node.attr == "_call" and isinstance(node.ctx, ast.Load) and not through_super:
            return [f"line {line}: calls _call past the guard"]
    if isinstance(node, ast.ClassDef):
        allowed = SUBCLASS_EXEMPTIONS.get(module, frozenset())
        bad = [
            b
            for b in _base_names(node)
            if b not in allowed
            and (
                b in CLIENT_CLASSES
                or b.split(".")[0] in {"functions", "types", "telethon"}
                or b.endswith("Request")
            )
        ]
        return [f"line {line}: class {node.name} extends {b}" for b in bad]
    return []


def violations(
    source: str, *, telegram: bool, own: frozenset[str] = frozenset(), module: str = ""
) -> list[str]:
    """Everything (a), (c) and, for a module of the telegram package, (b) find in `source`.
    `module` is the path under `tgmcp/` (with `/`), for the few per-module exemptions."""
    tree = ast.parse(source)
    bad: list[str] = []
    exempt = DYNAMIC_EXEMPTIONS.get(module, frozenset())
    for node in ast.walk(tree):
        bad += _denylist(node) + _tampering(node, module)
        if not (isinstance(node, ast.Name) and node.id in exempt):
            bad += _dynamic(node)
        if telegram:
            bad += _telethon(node, own)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            modules = [a.name for a in node.names]
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            bad += [f"line {node.lineno}: imports {m}" for m in modules if "telethon" in m]
        elif isinstance(node, ast.Attribute) and _chain(node)[0] == "functions":
            bad.append(f"line {node.lineno}: Telegram request outside tgmcp.telegram")
    return bad


def _modules() -> Iterator[tuple[Path, str]]:
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


OWN = _own_names([ast.parse(source) for _, source in _modules()])


def test_gateway_protocol_has_no_write_methods():
    names = [n for n in dir(gateway.Gateway) if not n.startswith("_")]
    assert names and not [n for n in names if is_forbidden(n)]


def test_whole_package_passes_the_checker():
    found = {
        str(path.relative_to(PACKAGE)): bad
        for path, source in _modules()
        if (
            bad := violations(
                source,
                telegram=TELEGRAM in path.parents,
                own=OWN,
                module=path.relative_to(PACKAGE).as_posix(),
            )
        )
    }
    assert found == {}


def test_only_the_cli_imports_the_telegram_package():
    for path, source in _modules():
        if path.name == "cli.py" or TELEGRAM in path.parents:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tgmcp.telegram"), path
                assert not (node.module == "tgmcp" and "telegram" in [a.name for a in node.names])


def test_the_live_gateway_builds_a_read_only_client(tmp_path: Path):
    settings = Settings(api_id=1, api_hash="a", hidden_chats=(), expected_user=None, home=tmp_path)
    built = TelethonGateway(settings, "")
    assert type(built._TelethonGateway__client) is ReadOnlyClient  # type: ignore[attr-defined]
    assert not [n for n in vars(built) if "client" in n and not n.startswith("_TelethonGateway")]


def test_every_allowed_request_is_a_read():
    """No request on either list may contain a write-shaped word, except the two sign-in
    requests `tgmcp login` needs, and only the login client may log out or sign in."""
    for request in ALLOWED_REQUESTS:
        assert not is_forbidden(request.__name__), request
    assert functions.auth.LogOutRequest not in ALLOWED_REQUESTS
    assert not {r for r in ALLOWED_REQUESTS if r.__module__.endswith(".auth")}


# --- (e) the bypasses of the review's S2 table, each must be flagged ------------------------

BYPASSES = {
    "A alltlobjects": (
        "from telethon.tl.alltlobjects import tlobjects\n"
        "await client(tlobjects[0x280D096F](peer=p, message='x', random_id=1))",
        True,
    ),
    "B vars and concatenation": (
        "req = vars(functions.messages)['Se' + 'ndMessageRequest']\nawait client(req(peer=p))",
        True,
    ),
    "C eval and compile": (
        "await eval(compile('client.se' + 'nd_message(p, \"x\")', '<s>', 'eval'))",
        False,
    ),
    "D computed getattr": (
        "name = bytes([115, 101, 110, 100]).decode()\nawait getattr(client, name)(p, 'x')",
        False,
    ),
    "E operator.attrgetter": (
        "import operator\noperator.attrgetter('se' + 'nd_message')(client)(p, 'x')",
        False,
    ),
    "F methods of a returned message": (
        "async for m in client.iter_messages(p, limit=1):\n"
        "    await m.respond('x')\n    await m.reply('y')\n    await m.click(0)",
        True,
    ),
    "G1 kick_participant": ("await client.kick_participant(p, u)", True),
    "G2 download_media": ("await client.download_media(m, 'x.jpg')", True),
    "G3 takeout": ("async with client.takeout() as t:\n    pass", True),
    "G4 inline_query": ("await client.inline_query('bot', 'q')", True),
    "G5 ReadMentionsRequest": ("await client(functions.messages.ReadMentionsRequest(p))", True),
    "G6 GetMessagesViews increment": (
        "await client(functions.messages.GetMessagesViewsRequest(p, [1], increment=True))",
        True,
    ),
    "G7 GetBotCallbackAnswer": (
        "await client(functions.messages.GetBotCallbackAnswerRequest(p, 1))",
        True,
    ),
    "G8 UpdateProfile": ("await client(functions.account.UpdateProfileRequest('x'))", True),
    "G9 SaveDraft": ("await client(functions.messages.SaveDraftRequest(p, 'x'))", True),
    "G10 Block": ("await client(functions.contacts.BlockRequest(p))", True),
    "G11 CreateChannel": (
        "await client(functions.channels.CreateChannelRequest('t', 'a'))",
        True,
    ),
    "G12 Report": ("await client(functions.messages.ReportRequest(p, [1], b'', 'x'))", True),
    "G13 ResetAuthorization": (
        "await client(functions.account.ResetAuthorizationRequest(1))",
        True,
    ),
    "G14 SetHistoryTTL": ("await client(functions.messages.SetHistoryTTLRequest(p, 1))", True),
    "G15 ClearAllDrafts": ("await client(functions.messages.ClearAllDraftsRequest())", True),
    "G16 StartBot": ("await client(functions.messages.StartBotRequest(b, p, 1, 'x'))", True),
    "H write from server.py through the gateway": (
        "async def _x(app):\n"
        "    await app.gateway._tg.send_message(p, 'x')\n"
        "    await app.gateway._tg(functions.messages.ReadHistoryRequest(peer=p, max_id=0))",
        False,
    ),
    "H3 an unlisted read request from server.py": (
        "await app.gateway._tg(functions.messages.GetMessagesViewsRequest(p, [1], True))",
        False,
    ),
    "H2 the mangled client": (
        "await app.gateway._TelethonGateway__client.get_participants(p)",
        False,
    ),
    "I __mro__ walk": ("for k in type(client).__mro__:\n    pass", True),
    "J vars of the class": ("names = vars(type(client))", True),
    "K f-string getattr": ("await getattr(client, f\"se{'nd'}_message\")(p, 'x')", True),
    "L the private sender": ("client._sender.send(r)", True),
    "M a telethon import outside the package": (
        "from telethon.tl.functions.messages import GetHistoryRequest",
        False,
    ),
    # The re-review's forgeries (N1): make the guard check one request and send another.
    "O1 an instance-level _bytes": (
        "req = functions.messages.GetHistoryRequest(p, 0, None, 0, 1, 0, 0, 0)\n"
        "req._bytes = lambda: payload\nawait client(req)",
        True,
    ),
    "O2 a __class__ swap": (
        "req.__class__ = functions.messages.GetHistoryRequest\nawait client(req)",
        True,
    ),
    "O3 an instance-level resolve": ("req.resolve = swap_back\nawait client(req)", True),
    "O4 replacing the guard's hook": ("ReadOnlyClient._call = TelegramClient._call", True),
    "O5 widening an instance's list": ("client.allowed = frozenset()", True),
    "O6 widening the class's list": ("type(client).allowed = frozenset()", True),
    "O7 a request subclass with its own bytes": (
        "class Evil(functions.messages.GetHistoryRequest):\n"
        "    def _bytes(self):\n        return payload",
        True,
    ),
    "O8 calling Telethon's _call past the guard": (
        "await TelegramClient._call(client, sender, req)",
        True,
    ),
    "O9 a __class__ swap outside the package": ("req.__class__ = Other", False),
    "O10 forging from an allowed class in cli.py": (
        "next(iter(ALLOWED_REQUESTS))(p)._bytes = forged",
        False,
    ),
    "O11 inspect reaches the mangled client": (
        "import inspect\nmembers = inspect.getmembers(app.gateway)",
        False,
    ),
    "O12 a client subclass outside client.py": (
        "class Wide(ReadOnlyClient):\n    pass",
        False,
    ),
    "O13 widening the module's list": ("client.ALLOWED_REQUESTS = frozenset()", False),
    "N an unlisted read is still refused inside the package": (
        "await client.get_participants(p)",
        True,
    ),
}


@pytest.mark.parametrize("label", sorted(BYPASSES))
def test_checker_flags_every_bypass(label: str):
    source, telegram = BYPASSES[label]
    assert violations(source, telegram=telegram, own=OWN), label


def test_checker_allows_read_only_properties_and_sentences():
    source = (
        "a = m.sender\nb = m.sender_id\nc = m.forward\nd = m.fwd_from\n"
        "e = getattr(x, 'username', None)\nf(sender)\n"
        "raise GatewayError('sender must be a user id or @username')\n"
        "g = ', '.join(names)\n"
    )
    assert violations(source, telegram=False) == []
    assert violations("t = m.reply_to\nh = m.raw_text\n", telegram=True, own=OWN) == []


# --- (d) the runtime allowlist ---------------------------------------------------------------

PEER = types.InputPeerUser(user_id=333, access_hash=1)

REFUSED = [
    functions.messages.SendMessageRequest(peer=PEER, message="x"),
    functions.messages.DeleteMessagesRequest(id=[1]),
    functions.messages.ReadHistoryRequest(peer=PEER, max_id=0),
    functions.messages.GetMessagesViewsRequest(peer=PEER, id=[1], increment=True),
    functions.account.UpdateProfileRequest(first_name="x"),
    functions.contacts.BlockRequest(id=PEER),
    functions.messages.SaveDraftRequest(peer=PEER, message="x"),
    functions.channels.JoinChannelRequest(channel=types.InputChannel(1, 1)),
    functions.auth.LogOutRequest(),
    functions.auth.SendCodeRequest("+15550100", 1, "a", types.CodeSettings()),
    functions.InvokeWithLayerRequest(1, functions.messages.SendMessageRequest(PEER, "x")),
    functions.InvokeWithoutUpdatesRequest(functions.messages.SendMessageRequest(PEER, "x")),
]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """What reached Telethon's own `_call` (which would put it on the wire)."""
    reached: list[Any] = []

    async def wire(self: Any, sender: Any, request: Any, **_kw: Any) -> Any:
        reached.append(request)
        return "ok"

    monkeypatch.setattr(TelegramClient, "_call", wire)
    return reached


def _client(cls: type[Any] = ReadOnlyClient) -> Any:
    return cls(StringSession(), 1, "a", receive_updates=False)


@pytest.mark.parametrize("request_", REFUSED, ids=lambda r: type(r).__name__)
async def test_read_only_client_refuses(request_: Any, sent: list[Any]):
    with pytest.raises(ReadOnlyViolation, match=type(request_).__name__):
        await _client()(request_)
    assert sent == []


async def test_a_batch_with_one_write_is_refused_whole(sent: list[Any]):
    batch = [functions.messages.GetHistoryRequest(PEER, 0, None, 0, 1, 0, 0, 0), REFUSED[0]]
    with pytest.raises(ReadOnlyViolation, match="SendMessageRequest"):
        await _client()(batch)
    assert sent == []


async def test_allowed_requests_reach_telethon(sent: list[Any]):
    request = functions.messages.GetHistoryRequest(PEER, 0, None, 0, 1, 0, 0, 0)
    assert await _client()(request) == "ok"
    assert sent == [request]


def _bound_message(client: Any) -> Any:
    m = types.Message(id=5, peer_id=types.PeerUser(333), date=None, message="hi")
    m._finish_init(client, {333: types.User(id=333, access_hash=1)}, PEER)
    return m


@pytest.mark.parametrize(
    ("method", "request_name"),
    [
        ("respond", "SendMessageRequest"),
        ("reply", "SendMessageRequest"),
        ("delete", "DeleteMessagesRequest"),
        ("mark_read", "ReadHistoryRequest"),
        ("pin", "UpdatePinnedMessageRequest"),
    ],
)
async def test_returned_message_methods_are_refused(
    method: str, request_name: str, sent: list[Any]
):
    message = _bound_message(_client())
    action = getattr(message, method)  # the test exercises a write path on purpose
    with pytest.raises(ReadOnlyViolation, match=request_name):
        await (action("x") if method in {"respond", "reply"} else action())
    assert sent == []


async def test_client_write_methods_are_refused(sent: list[Any]):
    client = _client()
    with pytest.raises(ReadOnlyViolation, match="SendMessageRequest"):
        await client.send_message(PEER, "x")
    with pytest.raises(ReadOnlyViolation, match="LogOutRequest"):
        await client(functions.auth.LogOutRequest())
    assert sent == []


async def test_auth_client_may_sign_in_and_log_out_but_not_write(sent: list[Any]):
    client = _client(AuthClient)
    assert await client(functions.auth.LogOutRequest()) == "ok"
    with pytest.raises(ReadOnlyViolation, match="SendMessageRequest"):
        await client(REFUSED[0])
    with pytest.raises(ReadOnlyViolation, match="GetHistoryRequest"):
        await client(functions.messages.GetHistoryRequest(PEER, 0, None, 0, 1, 0, 0, 0))
    assert sent == [functions.auth.LogOutRequest()]


def _history() -> Any:
    return functions.messages.GetHistoryRequest(PEER, 0, None, 0, 1, 0, 0, 0)


async def test_a_request_with_its_own_bytes_is_refused(sent: list[Any]):
    forged = _history()
    forged._bytes = lambda: bytes(REFUSED[0])  # GetHistory outside, SendMessage on the wire
    with pytest.raises(ReadOnlyViolation, match="own serialisation"):
        await _client()(forged)
    swapped = _history()

    async def swap(client: Any, utils: Any) -> None:
        swapped.__class__ = functions.messages.SendMessageRequest

    swapped.resolve = swap
    with pytest.raises(ReadOnlyViolation, match="own serialisation"):
        await _client()(swapped)
    assert sent == []


async def test_a_resolve_that_changes_the_class_is_caught_after_it(
    sent: list[Any], monkeypatch: pytest.MonkeyPatch
):
    async def becomes_a_write(self: Any, client: Any, utils: Any) -> None:
        self.__class__ = functions.messages.SendMessageRequest

    monkeypatch.setattr(functions.messages.GetHistoryRequest, "resolve", becomes_a_write)
    with pytest.raises(ReadOnlyViolation, match="SendMessageRequest"):
        await _client()(_history())
    assert sent == []


async def test_the_list_comes_from_the_module_not_the_instance(sent: list[Any]):
    client = _client()
    client.allowed = frozenset({functions.messages.SendMessageRequest})  # ignored
    with pytest.raises(ReadOnlyViolation, match="SendMessageRequest"):
        await client(REFUSED[0])

    class Wider(ReadOnlyClient):  # a subclass has no list of its own: it may send nothing
        pass

    with pytest.raises(ReadOnlyViolation, match="GetHistoryRequest"):
        await _client(Wider)(_history())
    assert sent == []


def test_the_login_client_may_follow_a_channel_gap():
    assert functions.updates.GetChannelDifferenceRequest in AUTH_REQUESTS
