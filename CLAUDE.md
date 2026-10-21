# tgmcp: read-only Telegram MCP server for Claude Code

Python package `tgmcp`. It exposes your own Telegram account to Claude Code through MCP, for reading only.

## Rules

1. **Read-only invariant.** No tool sends, edits, deletes, joins, leaves or marks anything as read. No Telethon call that changes state.
2. **`serve` never prints to stdout.** Stdout is the MCP protocol channel; logs and diagnostics go to stderr or a file.
3. **Encoding.** Every `open()`, `read_text()`, `write_text()` and `subprocess.run()` passes `encoding="utf-8"`; paths are `pathlib.Path`; files use LF line endings.
4. **Language.** Code, comments, docs and commit messages are in English.
5. **Done means `.venv/Scripts/python check.py` exits 0** (ruff format, ruff, mypy strict, pytest). The pre-commit hook runs `check.py --fast`; never use `--no-verify`.
6. **Contributions.** Work on a branch and open a pull request; `check.py` must be green; never use `--no-verify`. Never push to `main` directly.
7. Session files (`*.session`) and credentials never go into git.

Plans and specs live in `docs/`.
Setup, commands and the tool list are in `README.md`.
