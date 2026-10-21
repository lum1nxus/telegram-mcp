"""Run every check in order. Exit code 0 only when all of them pass.

Usage from the repository root:  .venv/Scripts/python check.py [--fast]

Steps: format, lint, types, tests. `--fast` is accepted for the pre-commit hook and is identical
to the full run until slow suites exist.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
# Force UTF-8 for every step: on Windows a piped child falls back to the console codec.
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

STEPS: list[tuple[str, list[str]]] = [
    ("format", [PY, "-m", "ruff", "format", "--check", "."]),
    ("lint", [PY, "-m", "ruff", "check", "."]),
    ("types", [PY, "-m", "mypy"]),
    ("tests", [PY, "-m", "pytest", "-q"]),
]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args != ["--fast"]:
        print(f"usage: python check.py [--fast]; got {args!r}")
        return 2
    for name, cmd in STEPS:
        print(f"== {name}", flush=True)
        if subprocess.run(cmd, cwd=ROOT, env=ENV, check=False).returncode != 0:
            print(f"FAILED: {name}")
            return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
