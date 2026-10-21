# tgmcp

A read-only Telegram connection for Claude Code.

**Who this is for.** Anyone who keeps useful things in Telegram chats (advice, links, group discussions) and wants to ask Claude Code about them. You do not need to be a programmer, only to be able to copy commands into a terminal.
**Not for you if** you want Claude to write messages for you: this tool can only read.

For developers: [CONTRIBUTING.md](CONTRIBUTING.md). What is next: [docs/ROADMAP.md](docs/ROADMAP.md). Security and the threat model: [SECURITY.md](SECURITY.md).

## Quick start

You need Python 3.14, Git, Claude Code and a Telegram API key (how to get one: "First run", step 1). Then, in a terminal:

1. `git clone https://github.com/lum1nxus/telegram-mcp`, then `cd telegram-mcp`
2. `py -3.14 -m venv .venv`
3. `.venv/Scripts/python -m pip install --require-hashes -r requirements.lock`, then `.venv/Scripts/python -m pip install --no-deps -e .`
4. `.venv/Scripts/tgmcp login`
5. `claude mcp add --scope user telegram -- C:\path\to\telegram-mcp\.venv\Scripts\tgmcp.exe serve` (replace the path with your folder)

In **cmd.exe** write the paths with backslashes: `.venv\Scripts\tgmcp login`. The details of every step are below.

## What it is

`tgmcp` lets Claude Code look into your own Telegram account: list your chats, search messages and read a conversation. It runs on your computer, logs in as you (through your own Telegram API key) and answers only when Claude asks.

### What it cannot do

- It cannot send, edit, delete or forward messages, react, or mark anything as read.
- It cannot join, leave or pin chats.
- It cannot download media; a message with a photo or a file only shows that one is there.
- It keeps no copy of your chats on disk. Messages are fetched from Telegram when asked and forgotten afterwards.

## First run

Every command below is typed in a terminal: open **Git Bash**, **PowerShell** or **cmd.exe**, then go to the folder you cloned the repository into:

```
cd C:\path\to\telegram-mcp
```

Replace `C:\path\to\telegram-mcp` with the folder you cloned into (here and in every command below). All later commands are run from that folder. They start with `.venv/Scripts/...`, which means "inside this folder". Git Bash and PowerShell accept this form; **cmd.exe needs backslashes** (`.venv\Scripts\tgmcp login`).

1. Get an API key. Open https://my.telegram.org, log in with your phone number, choose "API development tools" and create an application (any name and description). Note the `api_id` (a number) and the `api_hash` (a long string of letters and digits).
2. Create the environment (once). Python 3.14 must be installed:

   ```
   py -3.14 -m venv .venv
   .venv/Scripts/python -m pip install --require-hashes -r requirements.lock
   .venv/Scripts/python -m pip install --no-deps -e .
   ```

   (cmd.exe: `.venv\Scripts\python -m pip ...`.)

3. Log in:

   ```
   .venv/Scripts/tgmcp login
   ```

   cmd.exe: `.venv\Scripts\tgmcp login`. It asks, in this order:
   - `API id:` and `API hash:` (first time only). The hash is not shown while you type; paste it and press Enter. The API id is saved in `config.json`; the hash is saved only in the encrypted `secrets.bin`.
   - Your phone number, with the country code, for example `+48 600 123 456`.
   - The code. It arrives as a message **inside the Telegram app** (from the "Telegram" service chat), not by SMS.
   - Your two-step verification password, only if you have set one in Telegram.

   Telethon (the library tgmcp uses) may print a line about Telegram's Terms of Service; nothing needs to be done. At the end it prints `logged in as <your name>`.

   If you typed the API id or hash wrong, Telegram rejects them; tgmcp forgets them and asks again next time. To replace saved ones by hand: `.venv/Scripts/tgmcp login --reset` (it also forgets the saved login, so Telegram sends a new code; the old login stays in Telegram's Settings, Devices list until you end it there, or run `tgmcp logout` before `--reset`).

4. Check that it works:

   ```
   .venv/Scripts/tgmcp whoami
   ```

   cmd.exe: `.venv\Scripts\tgmcp whoami`. It prints your account, the number of chats and the hidden chats.

5. Register it with Claude Code (once):

   ```
   claude mcp add --scope user telegram -- C:\path\to\telegram-mcp\.venv\Scripts\tgmcp.exe serve
   ```

   This line is the same in every terminal. Then start a new Claude Code session; `telegram` appears in `/mcp`. If the `claude` command is not found, see "Troubleshooting".

Other commands: `.venv/Scripts/tgmcp logout` ends the login on Telegram and removes `secrets.bin`.

## Using it from Claude Code

Start Claude Code (`claude`) in any folder and ask in plain words. Examples:

- "What did my friends recommend about hiking boots last spring?"
- "Summarise the last two weeks of the Project X group."
- "Find the message where someone shared the apartment contract link."

Claude chooses the tools itself (list chats, search, read around a message). The first time it uses each tool, Claude Code asks for your permission; you can allow it once or always. All six tools only read.

Tips: say which chat or period you mean when you know it; answers are faster and shorter. Telegram's search finds word beginnings, not fragments (see "Search semantics"), so if Claude finds nothing it will usually try other forms of the word.

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

## Privacy and what the tool can see

- It sees everything your Telegram account sees (private chats, groups, channels, archived chats), except the chats you hide. What a tool returns goes to Claude as part of your conversation, like anything else you let Claude read.
- Forwarded messages name where they came from (`forwarded_from`), even when the source chat is hidden.
- A channel's comments live in its linked discussion group, which holds a copy of every post.
- Nothing leaves your PC except requests to Telegram's servers and the tool answers that go to Claude. There is no telemetry. Logs hold tool names, counts and errors, never message text.
- A chat can contain text that looks like instructions to Claude. The tools tell Claude to treat message text as data, but stay alert if an answer suggests something odd. See [SECURITY.md](SECURITY.md).

### Hidden chats

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

On systems other than Windows there is no encryption: `secrets.bin` is a plain file protected only by its `0600` mode, and `whoami` prints a warning.

If an administrator resets your Windows password (instead of you changing it yourself), Windows may no longer be able to decrypt `secrets.bin`. tgmcp then says so; run `.venv/Scripts/tgmcp login` again.

Versions before 21.10.2026 kept the hash in `config.json` and the login in `telegram.session`, both in clear text. If those files are still there, tgmcp says "Old clear-text files found"; run `.venv/Scripts/tgmcp login` once. It moves the existing login into `secrets.bin` (no new code from Telegram) and removes the old files. Removing them does not shred them: copies in backups, cloud sync or Windows shadow copies stay valid until you run `.venv/Scripts/tgmcp logout`.

## Several Claude sessions and the VPN caveat

Each Claude Code session starts its own `tgmcp serve`, and each holds its own connection with the same login. That is fine. But Telegram rejects the same login from two IP addresses at once, so switching a VPN in the middle of a session can break it. Then run `.venv/Scripts/tgmcp logout`, then `.venv/Scripts/tgmcp login`.

## Troubleshooting

**`claude` is not found (the `claude mcp add` command fails).** Add the server by hand. Close Claude Code, open `.claude.json` in your user folder (`C:\Users\<you>\.claude.json`) in a text editor and add a `telegram` entry inside the top-level `mcpServers` object (create the object if it is missing; keep the file valid JSON, with doubled backslashes):

```json
{
  "mcpServers": {
    "telegram": {
      "type": "stdio",
      "command": "C:\\path\\to\\telegram-mcp\\.venv\\Scripts\\tgmcp.exe",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

If the file already has other content, add only the `"telegram": { ... }` part inside the existing `mcpServers`, with a comma after the neighbouring entry. Then start a new Claude Code session.

**`telegram ✘ failed` in `/mcp`.** Open a terminal in the repository folder and run `.venv/Scripts/tgmcp whoami`. It prints one sentence saying what is wrong (for example, "Not logged in: run `tgmcp login` in a terminal."); do what it says, then start a new Claude Code session. Also check that the path in the registration still exists (the folder was not moved or renamed).

**"Telegram no longer accepts this session".** The login was ended (for example in Telegram's Devices list) or was used from two IP addresses. Run `.venv/Scripts/tgmcp logout`, then `.venv/Scripts/tgmcp login`.

**"The secrets file cannot be decrypted on this computer or user account".** `secrets.bin` was made under another Windows user or on another PC, or an administrator reset your password. It cannot be recovered; run `.venv/Scripts/tgmcp login` again (it writes a new file). Copying `secrets.bin` between computers does not work by design.

**"Telegram asks to wait N seconds before the next request".** This is Telegram's flood protection. Wait the stated time, then ask for less: a narrower period, a smaller `limit`, one chat instead of all. Claude is told not to retry sooner.

**A search finds nothing, but you know the message exists.** Telegram matches word beginnings only. Ask Claude to try other forms of the word, or to search inside one chat.

**Python 3.14 is not found.** Install it from python.org (keep the "py launcher" option ticked), then repeat step 2.

## Uninstall

1. End the login: `.venv/Scripts/tgmcp logout`. This signs the device out of Telegram and removes `secrets.bin`.
2. Remove it from Claude Code: `claude mcp remove telegram --scope user` (or delete the `telegram` entry from `mcpServers` in `.claude.json`).
3. Delete the data folder `%LOCALAPPDATA%\telegram-mcp` (or the folder in `TGMCP_HOME`), and the repository folder.
4. In the Telegram app open Settings, Devices and check that the session is gone; end it there if it is still listed.

The API key you created at https://my.telegram.org stays with your Telegram account; keep the `api_hash` private.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: install `requirements-dev.lock`, run `.venv/Scripts/python check.py`, and remember that `serve` never writes to stdout, because that channel belongs to the MCP protocol.
