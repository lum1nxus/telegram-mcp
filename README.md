# tgmcp

A read-only Telegram connection for Claude Code.

## What it is

`tgmcp` lets Claude Code look into your own Telegram account: list your chats, search messages and read a conversation. It runs on your computer, logs in as you (through your own Telegram API key) and answers only when Claude asks.

### What it cannot do

- It cannot send, edit, delete or forward messages, react, or mark anything as read.
- It cannot join, leave or pin chats.
- It cannot download media; a message with a photo or a file only shows that one is there.
- It keeps no copy of your chats on disk. Messages are fetched from Telegram when asked and forgotten afterwards.

## Setup

1. Open https://my.telegram.org, log in with your phone number, choose "API development tools" and create an application (any name). Note the `api_id` (a number) and the `api_hash` (a long string).
2. Create the environment (once):

   ```
   py -3.14 -m venv .venv
   .venv/Scripts/python -m pip install --require-hashes -r requirements-dev.lock
   .venv/Scripts/python -m pip install --no-deps -e .
   ```

3. Log in. It asks for the API id and hash (first time only), then Telegram sends a code to your app; type the code, and the 2FA password if you have one:

   ```
   .venv/Scripts/tgmcp login
   ```

4. Check that it works:

   ```
   .venv/Scripts/tgmcp whoami
   ```

5. Register it with Claude Code:

   ```
   claude mcp add --scope user telegram -- C:\path\to\telegram-mcp\.venv\Scripts\tgmcp.exe serve
   ```

Other commands: `tgmcp logout` ends the login on Telegram and deletes the session file.

## Tools

Every tool is annotated `read-only: yes, open world: no`.

| Tool | Arguments (defaults) | Annotations |
|---|---|---|
| `list_chats` | `query` (none), `kind` (none: user, group or channel), `include_archived` (false), `limit` (50, 1 to 200) | read-only: yes, open world: no |
| `get_chat` | `chat` (id, @username, title or part of it) | read-only: yes, open world: no |
| `list_topics` | `chat`, `limit` (100, 1 to 200) | read-only: yes, open world: no |
| `search_messages` | `query`, `chat` (none: all chats), `topic` (none), `sender` (none: user id or @username), `since` (none, date), `until` (none, date), `limit` (30, 1 to 100) | read-only: yes, open world: no |
| `get_messages` | `chat`, `topic` (none), `since` (none), `until` (none), `before_id` (none), `limit` (50, 1 to 100) | read-only: yes, open world: no |
| `get_message_context` | `chat`, `message_id`, `before` (10, 0 to 30), `after` (10, 0 to 30) | read-only: yes, open world: no |

## Search semantics

Telegram search matches the beginnings of words, not fragments: "granite" finds "graniteu", but "zing" finds nothing. When a search finds nothing, try another form of the word before concluding that the message does not exist.

## Hidden chats

Chats you do not want Claude to see can be hidden. List them (by id, title or @username) in `config.json` under `hidden_chats`, or set the environment variable `TGMCP_HIDDEN_CHATS` to a comma-separated list. Hidden chats never appear in any tool. `tgmcp whoami` shows how many are hidden and whether each entry matched a chat.

## Where things are stored

Everything lives in one folder: `%LOCALAPPDATA%\telegram-mcp` on Windows (or the folder in `TGMCP_HOME`).

- `config.json`: API id and hash, hidden chats.
- `telegram.session`: the saved login.
- `tgmcp.log`: a technical log without message text.

The session file is the account: anyone who copies it is logged in as you. Keep it out of git, backups you share and cloud folders. `tgmcp login` restricts the folder to your Windows user. If it leaks, run `tgmcp logout`, or end the session in Telegram under Settings, Devices.

## Several Claude sessions and the VPN caveat

Each Claude Code session starts its own `tgmcp serve`, and each holds its own connection with the same login. That is fine. But Telegram rejects the same login from two IP addresses at once, so switching a VPN in the middle of a session can break it. Then run `tgmcp login` again.

## Development

- Enable the hook once per clone: `git config core.hooksPath .githooks`. It refuses a commit when `git config user.email` does not end with `@users.noreply.github.com`, and runs `check.py --fast`.
- Done means `.venv/Scripts/python check.py` exits 0 (formatting, lint, strict types, tests).
- `serve` never writes to stdout: that channel belongs to the MCP protocol.
