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
PACKAGE = Path(__file__).resolve().parent.parent / "tgmcp"


def test_gateway_protocol_has_no_write_methods():
    names = [n for n in dir(gateway.Gateway) if not n.startswith("_")]
    assert names and not [n for n in names if any(w in n for w in FORBIDDEN)]


def test_only_gateway_imports_telethon():
    for path in PACKAGE.glob("*.py"):
        if path.name != "gateway.py":
            assert "telethon" not in path.read_text(encoding="utf-8")


def test_no_write_names_anywhere_in_package():
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                assert not any(w in node.name for w in FORBIDDEN), f"{path}:{node.name}"
