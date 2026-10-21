# tgmcp: read-only Telegram MCP server

Python package `tgmcp`. It exposes your own Telegram account to any MCP client or local model, for reading only.

## Rules

1. **Read-only invariant.** No tool sends, edits, deletes, joins, leaves or marks anything as read. A new Telegram request or Telethon name goes through the allowlists (CONTRIBUTING.md).
2. **`serve` never prints to stdout.** Stdout is the MCP protocol channel; logs go to stderr and the log file. Never log message text.
3. **Encoding.** Every `open()`, `read_text()`, `write_text()` and `subprocess.run()` passes `encoding="utf-8"`; paths are `pathlib.Path`; files use LF line endings.
4. **Language.** Code, comments, docs and commit messages are in English.
5. **Done means `.venv/Scripts/python check.py` exits 0** (ruff format, ruff, mypy strict, pytest). The pre-commit hook runs `check.py --fast`; never use `--no-verify`.
6. **Contributions.** Work on a branch and open a pull request; never push to `main` directly.
7. Session strings, `secrets.bin` and credentials never go into git.

Code: `tgmcp/` (server and tools), `tgmcp/telegram/` (Telethon and the request allowlists), `tgmcp/host/` (local models, bench).
User docs: `README.md` and `docs/` (setup, local-models, tools, privacy). Design history: `docs/history/`.
