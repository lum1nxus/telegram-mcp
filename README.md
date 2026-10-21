# tgmcp

A read-only Telegram connection for Claude Code.

## What it is

`tgmcp` lets Claude Code look into your own Telegram account: list your chats, search messages and read a conversation. It runs on your computer, logs in as you (through your own Telegram API key) and answers only when Claude asks.

### What it cannot do

- It cannot send, edit, delete or forward messages, react, or mark anything as read.
- It cannot join, leave or pin chats.
- It cannot download media; a message with a photo or a file only shows that one is there.
- It keeps no copy of your chats on disk. Messages are fetched from Telegram when asked and forgotten afterwards.

## First run

Every command below is typed in a terminal: open **Git Bash** or **PowerShell**, then go to the repository folder first:

```
cd C:\path\to\telegram-mcp
```

Replace `C:\path\to\telegram-mcp` with the folder you cloned into (here and in the registration command below). All later commands are run from that folder (they start with `.venv/Scripts/...`, which means "inside this folder").

1. Get an API key. Open https://my.telegram.org, log in with your phone number, choose "API development tools" and create an application (any name and description). Note the `api_id` (a number) and the `api_hash` (a long string of letters and digits).
2. Create the environment (once). Python 3.14 must be installed:

   ```
   py -3.14 -m venv .venv
   .venv/Scripts/python -m pip install --require-hashes -r requirements.lock
   .venv/Scripts/python -m pip install --no-deps -e .
   ```

3. Log in:

   ```
   .venv/Scripts/tgmcp login
   ```

   It asks, in this order:
   - `API id:` and `API hash:` (first time only). The hash is not shown while you type; paste it and press Enter. The API id is saved in `config.json`; the hash is saved only in the encrypted `secrets.bin`.
   - Your phone number, with the country code, for example `+1 555 0100`.
   - The code. It arrives as a message **inside the Telegram app** (from the "Telegram" service chat), not by SMS.
   - Your two-step verification password, only if you have set one in Telegram.

   Telethon (the library tgmcp uses) may print a line about Telegram's Terms of Service; nothing needs to be done. At the end it prints `logged in as <your name>`.

   If you typed the API id or hash wrong, Telegram rejects them; tgmcp forgets them and asks again next time. To replace saved ones by hand: `.venv/Scripts/tgmcp login --reset` (it also forgets the saved login, so Telegram sends a new code; the old login stays in Telegram's Settings, Devices list until you end it there, or run `tgmcp logout` before `--reset`).

4. Check that it works:

   ```
   .venv/Scripts/tgmcp whoami
   ```

   It prints your account, the number of chats and the hidden chats.

5. Register it with Claude Code (once):

   ```
   claude mcp add --scope user telegram -- C:\path\to\telegram-mcp\.venv\Scripts\tgmcp.exe serve
   ```

   Then start a new Claude Code session; `telegram` appears in `/mcp`.

Other commands: `.venv/Scripts/tgmcp logout` ends the login on Telegram and removes `secrets.bin`.

### If Claude Code shows `telegram ✘ failed`

Open a terminal in the repository folder and run `.venv/Scripts/tgmcp whoami`. It prints one sentence saying what is wrong (for example, "Not logged in: run `tgmcp login` in a terminal."); do what it says, then start a new Claude Code session.

If Claude reports "Telegram no longer accepts this session", run `.venv/Scripts/tgmcp logout`, then `.venv/Scripts/tgmcp login`.

## Tools

Every tool is read-only and does not reach beyond your account (annotations: read-only yes, open world no).

| Tool | Arguments (defaults) |
|---|---|
| `list_chats` | `query` (none), `kind` (none: user, group or channel), `include_archived` (false), `limit` (50, 1 to 200) |
| `get_chat` | `chat` (id, @username, title or part of it) |
| `list_topics` | `chat`, `limit` (100, 1 to 200) |
| `search_messages` | `query` (not empty), `chat` (none: all chats), `topic` (none; needs `chat`), `sender` (none: user id or @username; needs `chat`), `since` (none, a date), `until` (none, a date), `limit` (30, 1 to 100) |
| `get_messages` | `chat`, `topic` (none), `since` (none, a date and time), `until` (none, a date and time), `before_id` (none, at least 1), `limit` (50, 1 to 100) |
| `get_message_context` | `chat`, `message_id` (at least 1), `before` (10, 0 to 30), `after` (10, 0 to 30) |

Dates and times are UTC: `since` and `until` of `search_messages` are whole UTC days (both included), and a date and time without a time zone in `get_messages` is taken as UTC. If you live east of Greenwich, a message sent just after your local midnight belongs to the previous UTC day.

## Search semantics

Telegram search matches the beginnings of words, not fragments: "market" finds "marketing", but "ket" finds nothing. When a search finds nothing, try another form of the word before concluding that the message does not exist.

## Hidden chats

Chats you do not want Claude to see can be hidden. List them (by id, title or @username) in `config.json` under `hidden_chats`, or set the environment variable `TGMCP_HIDDEN_CHATS` to a comma-separated list (an empty variable counts as not set). A hidden chat is left out of every tool: it is not listed, cannot be opened by id, name or title, and global search drops its messages. `tgmcp whoami` shows how many are hidden and whether each entry matched a chat.

What hiding does not cover:

- A message forwarded from a hidden chat into a visible one still shows, and its `forwarded_from` names where it came from.
- A channel's comments live in its linked discussion group, which holds a copy of every post. Hiding the channel does not hide that group: hide the group too if needed (`get_chat` on the channel shows its `discussion_chat_id`).
- Hiding a private chat with a person does not hide that person's messages in groups.

## Where things are stored

Everything lives in one folder: `%LOCALAPPDATA%\telegram-mcp` on Windows (or the folder in `TGMCP_HOME`).

- `secrets.bin`: the API hash and the login key, encrypted with Windows DPAPI for your Windows user account. A copy is useless on another PC or under another Windows user, unless the attacker also has your Windows profile's DPAPI master keys and your password (a full profile backup). It still opens for any program running as you, so keep the PC clean.
- `config.json`: the settings that are not secret: API id, hidden chats.
- `tgmcp-<number>.log`: a technical log without message text, one file per running server (the number is its process id). Files older than 7 days are removed automatically.

When tgmcp creates the folder, it makes it readable only by your Windows user (a folder that already exists is left as it is), and `secrets.bin` is restricted the same way. If you think the login key leaked, run `.venv/Scripts/tgmcp logout`, or end the session in Telegram under Settings, Devices.

If an administrator resets your Windows password (instead of you changing it yourself), Windows may no longer be able to decrypt `secrets.bin`. tgmcp then says so; run `.venv/Scripts/tgmcp login` again.

Versions before 21.10.2026 kept the hash in `config.json` and the login in `telegram.session`, both in clear text. If those files are still there, tgmcp says "Old clear-text files found"; run `.venv/Scripts/tgmcp login` once. It moves the existing login into `secrets.bin` (no new code from Telegram) and removes the old files. Removing them does not shred them: copies in backups, cloud sync or Windows shadow copies stay valid until you run `.venv/Scripts/tgmcp logout`.

## Several Claude sessions and the VPN caveat

Each Claude Code session starts its own `tgmcp serve`, and each holds its own connection with the same login. That is fine. But Telegram rejects the same login from two IP addresses at once, so switching a VPN in the middle of a session can break it. Then run `.venv/Scripts/tgmcp logout`, then `.venv/Scripts/tgmcp login`.

## Development

- Install the development tools instead of step 2's first `pip` line: `.venv/Scripts/python -m pip install --require-hashes -r requirements-dev.lock`.
- Enable the hook once per clone: `git config core.hooksPath .githooks`. It refuses a commit when `git config user.email` does not end with `@users.noreply.github.com`, and runs `check.py --fast`.
- Done means `.venv/Scripts/python check.py` exits 0 (formatting, lint, strict types, tests).
- `serve` never writes to stdout: that channel belongs to the MCP protocol.
