# tgmcp

Read-only Telegram MCP server: let any model list, search and read your own chats. Secrets encrypted at rest.

Read-only; the list below says what it never does. It works with any MCP client (MCP: the standard way AI apps plug in tools; for example Claude Code or LM Studio), and `tgmcp ask` answers with a model on your own PC.

## Who it is for

Anyone who keeps useful things in Telegram chats (advice, links, group discussions) and wants to ask a model about them.
You do not need to program, only to copy commands into a terminal on Windows.

## Quick start

You need Python 3.14 and Git. The commands are for **cmd.exe**; in Git Bash write `.venv/Scripts/...` instead of `.venv\Scripts\...`. [docs/setup.md](docs/setup.md) explains every step.

1. Get a Telegram API key: open https://my.telegram.org, log in, choose "API development tools", create an application, note `api_id` and `api_hash`.
2. `git clone https://github.com/lum1nxus/telegram-mcp && cd telegram-mcp`
3. `py -3.14 -m venv .venv && .venv\Scripts\python -m pip install --require-hashes -r requirements.lock && .venv\Scripts\python -m pip install --no-deps -e .`
4. `.venv\Scripts\tgmcp login` (it asks for the API id, the API hash, your phone number, the code Telegram sends (usually inside the Telegram app) and your two-step password if you set one)
5. `claude mcp add --scope user telegram -- "C:\full\path\telegram-mcp\.venv\Scripts\tgmcp.exe" serve`, with the full path of your cloned folder (`cd` in cmd.exe prints it). This is for Claude Code; other clients: [docs/setup.md](docs/setup.md).
6. Restart your client (in Claude Code: start a new session).

Then ask your client or model about your chats.

With a local model instead (Ollama installed, model pulled): `.venv\Scripts\tgmcp ask "What did Nora say about the boots?" --model gemma4:12b`. See [docs/local-models.md](docs/local-models.md).

## What it can do

Six tools, all read-only. Details, arguments and limits: [docs/tools.md](docs/tools.md).

| Tool | What it does |
|---|---|
| `list_chats` | lists your chats, filtered by name or kind |
| `get_chat` | details of one chat: members, linked discussion group |
| `list_topics` | the topics of a forum group |
| `search_messages` | searches messages in one chat or in all chats, also trying other word forms, the other script and common compounds |
| `get_messages` | reads a chat's history, newest first, page by page |
| `get_message_context` | one message with the messages around it |

## What it never does

- Send, edit, delete, forward or react to messages, or mark anything as read.
- Join, leave or pin chats.
- Download photos or files: a message only says that it has one.
- Keep a copy of your chats on disk.

## Privacy in one paragraph

Your Telegram login and API hash are stored in one file, encrypted with Windows DPAPI so that only your Windows user on this PC can open it. Nothing from your chats is written to disk; the log holds chat ids, counts and error types, never message text. What a tool returns goes to the model you use: with a cloud client such as Claude Code it goes to that company's servers, with a local model it stays on your PC. Details, limits and the threat model: [docs/privacy.md](docs/privacy.md).

## Documentation

| Page | Read it when |
|---|---|
| [docs/setup.md](docs/setup.md) | you install it, connect it to a client, something fails, or you remove it |
| [docs/local-models.md](docs/local-models.md) | you want a model on your own PC (LM Studio, Ollama) or want to compare models |
| [docs/tools.md](docs/tools.md) | you want to know exactly what each tool takes and returns |
| [docs/privacy.md](docs/privacy.md) | you want to know what it can see, where things are stored, and what it does not protect against |
| [docs/ROADMAP.md](docs/ROADMAP.md) | you wonder what may come next |
| [CONTRIBUTING.md](CONTRIBUTING.md) | you want to change the code |
| [SECURITY.md](SECURITY.md) | you found a security problem |

## Status

- Version 0.1.0: the six tools (v1) and local models (`tgmcp ask`, `tgmcp bench`) are merged.
- Not yet verified live: the LM Studio path (chat window and `--backend lmstudio`), and a full `tgmcp bench` on the candidate models.
- Pinned versions: Telethon 1.45, mcp 2.3, Python 3.14 for the locked install.

## License

MIT, see [LICENSE](LICENSE).
