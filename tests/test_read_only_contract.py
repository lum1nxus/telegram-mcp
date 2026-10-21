import ast
from pathlib import Path

from tgmcp import gateway

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
)
# Read-only Telethon properties whose names contain a forbidden word (Attribute nodes only).
ALLOWED_ATTRIBUTES = frozenset({"sender", "sender_id", "forward", "fwd_from"})
PACKAGE = Path(__file__).resolve().parent.parent / "tgmcp"


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


_WORDS = tuple(_norm(w) for w in FORBIDDEN)


def is_forbidden(name: str) -> bool:
    """Case-insensitive, underscores ignored: `ReadHistoryRequest` matches `read_history`."""
    normal = _norm(name)
    return any(w in normal for w in _WORDS)


def _defined_names(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.append((node.lineno, node.name))
    return found


def _getattr_problem(node: ast.Call) -> bool:
    if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
        return False
    name = node.args[1] if len(node.args) > 1 else None
    return not (isinstance(name, ast.Constant) and isinstance(name.value, str))


def _node_problems(node: ast.AST) -> list[str]:
    line = getattr(node, "lineno", 0)
    if isinstance(node, ast.Attribute):
        if node.attr not in ALLOWED_ATTRIBUTES and is_forbidden(node.attr):
            return [f"line {line}: .{node.attr}"]
    elif isinstance(node, ast.Import | ast.ImportFrom):
        names = [n for a in node.names for n in (a.name, a.asname or "")]
        return [f"line {line}: import {n}" for n in names if is_forbidden(n.rsplit(".", 1)[-1])]
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value.strip()
        if text.isidentifier() and is_forbidden(text):
            return [f"line {line}: {text!r}"]
    elif isinstance(node, ast.Call) and _getattr_problem(node):
        return [f"line {line}: getattr with a computed name"]
    return []


def gateway_violations(source: str) -> list[str]:
    """Every write-shaped name the gateway source touches.

    Checks attribute reads (minus the read-only allowlist), imported names, defined names,
    identifier-like string constants (the kind a dynamic lookup would use; sentences are not
    identifiers) and `getattr` calls whose name is not a string constant.
    """
    tree = ast.parse(source)
    bad = [f"line {line}: def {name}" for line, name in _defined_names(tree) if is_forbidden(name)]
    for node in ast.walk(tree):
        bad.extend(_node_problems(node))
    return bad


def test_gateway_protocol_has_no_write_methods():
    names = [n for n in dir(gateway.Gateway) if not n.startswith("_")]
    assert names and not [n for n in names if is_forbidden(n)]


def test_only_gateway_imports_telethon():
    for path in PACKAGE.rglob("*.py"):
        if path.name != "gateway.py":
            assert "telethon" not in path.read_text(encoding="utf-8")


def test_no_write_names_anywhere_in_package():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, name in _defined_names(tree):
            assert not is_forbidden(name), f"{path}:{line}:{name}"


def test_gateway_calls_no_write_attributes():
    source = (PACKAGE / "gateway.py").read_text(encoding="utf-8")
    assert gateway_violations(source) == []


def test_checker_catches_write_requests():
    cases = [
        "client(functions.messages.ReadHistoryRequest(peer=p, max_id=0))",
        "client(functions.messages.SendMessageRequest(peer=p, message='x'))",
        "client(functions.channels.DeleteMessagesRequest(channel=p, id=[1]))",
        "client(functions.messages.ForwardMessagesRequest(to_peer=p, id=[1]))",
        "client(functions.messages.UpdatePinnedMessageRequest(peer=p, id=1))",
        "client(functions.channels.JoinChannelRequest(channel=p))",
        "from telethon.tl.functions.messages import ReadHistoryRequest as R",
        "await client.send_message(p, 'x')",
        "getattr(client, 'send_message')",
        "getattr(client, name)",
        "def markRead(): pass",
    ]
    for source in cases:
        assert gateway_violations(source), source


def test_checker_allows_read_only_properties_and_sentences():
    source = (
        "a = m.sender\nb = m.sender_id\nc = m.forward\nd = m.fwd_from\n"
        "e = getattr(x, 'username', None)\n"
        "raise GatewayError('sender must be a user id or @username')\n"
    )
    assert gateway_violations(source) == []
