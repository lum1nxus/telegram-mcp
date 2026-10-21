# Setup

For anyone installing tgmcp on Windows for the first time, connecting it to a client, fixing a failed start, or removing it.

## Before you start

You need Python 3.14 (from python.org, with the "py launcher" option ticked), Git, and a Telegram API key:

1. Open https://my.telegram.org and log in with your phone number.
2. Choose "API development tools" and create an application (any name and description).
3. Note the `api_id` (a number) and the `api_hash` (a long string of letters and digits).

## Install

Open a terminal (cmd.exe, PowerShell or Git Bash) in the folder where you keep projects. Every command after the first two is run from the cloned folder.

### cmd.exe

```
git clone https://github.com/lum1nxus/telegram-mcp
```

```
cd telegram-mcp
```

```
py -3.14 -m venv .venv
```

```
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
```

```
.venv\Scripts\python -m pip install --no-deps -e .
```

### Git Bash or PowerShell

The same commands, with forward slashes in the paths:

```
.venv/Scripts/python -m pip install --require-hashes -r requirements.lock
```

```
.venv/Scripts/python -m pip install --no-deps -e .
```

The rest of this page writes the cmd.exe form (`.venv\Scripts\tgmcp`); in Git Bash and PowerShell use `.venv/Scripts/tgmcp`.

## Log in

```
.venv\Scripts\tgmcp login
```

It asks, in this order:

- `API id:` and `API hash:` (first time only). The hash is not shown while you type: paste it and press Enter.
- Your phone number, with the country code.
- The code. It arrives as a message inside the Telegram app, not by SMS.
- Your two-step verification password, only if you set one in Telegram.

At the end it prints `logged in as <your name>`. If Telegram rejects the API id or hash, tgmcp forgets them and asks again next time.

To enter a new API id and hash, run `tgmcp login --reset`. It also forgets the saved login, so Telegram sends a new code. The old login stays in Telegram's Settings, Devices list until you end it there; run `tgmcp logout` before `--reset` to avoid that.

Check that it works:

```
.venv\Scripts\tgmcp whoami
```

It prints your account, the number of chats (archived and hidden among them), each hidden-chat entry and whether it matched a chat, and the data folder.

## Connect a client

tgmcp is an MCP server that runs on standard input and output. Any MCP client that can start such a server works the same way: the command is the full path to `.venv\Scripts\tgmcp.exe` in your cloned folder, with the single argument `serve`. Each client starts its own copy; you never run `tgmcp serve` yourself.

### Claude Code

In cmd.exe, from the cloned folder (`%CD%` is the current folder):

```
claude mcp add --scope user telegram -- "%CD%\.venv\Scripts\tgmcp.exe" serve
```

In Git Bash, from the cloned folder:

```
claude mcp add --scope user telegram -- "$(pwd -W)/.venv/Scripts/tgmcp.exe" serve
```

Start a new Claude Code session; `telegram` appears in `/mcp`. The first time each tool is used, Claude Code asks for your permission.

If the `claude` command is not found, add the server by hand. Close Claude Code and open `.claude.json` in your user folder in a text editor. Inside the top-level `mcpServers` object (create it if it is missing) add the `telegram` entry below. Replace `FOLDER` with the full path of your cloned folder (cmd.exe prints it with `cd`), and double every backslash:

```json
{
  "mcpServers": {
    "telegram": {
      "type": "stdio",
      "command": "FOLDER\\.venv\\Scripts\\tgmcp.exe",
      "args": ["serve"],
      "env": {}
    }
  }
}
```

If `mcpServers` already holds other servers, add only the `"telegram": { ... }` part, with a comma after the neighbouring entry.

### LM Studio

LM Studio reads the same kind of entry from its `mcp.json`. The steps are in [local-models.md](local-models.md).

## Ask

Ask in plain words, and name the chat or the period when you know it; answers are faster and shorter. Examples:

- "What did my friends recommend about hiking boots last spring?"
- "Summarise the last two weeks of the Project X group."
- "Find the message where someone shared the apartment contract link."

Telegram's search matches the beginnings of words, so the model may try several forms of a word ([tools.md](tools.md)).

## Troubleshooting

### The server shows as failed in the client

Run `tgmcp whoami` in the cloned folder. It prints one sentence saying what is wrong (for example "Not logged in: run `tgmcp login` in a terminal."); do what it says and start a new session. Also check that the folder in the registered path still exists.

### "This session belongs to another account."

`expected_user` is set (see [privacy.md](privacy.md)) and the stored login is for another account. Log in with the right account, or change the setting.

### Telegram no longer accepts the login

The login was ended (for example in Telegram's Devices list) or was used from two IP addresses at once. Run `tgmcp logout`, then `tgmcp login`.

### "The secrets file cannot be decrypted on this computer or user account"

`secrets.bin` was made under another Windows user or on another PC, or an administrator reset your password. Run `tgmcp login` again; it writes a new file.

### "Old clear-text files found"

You used a version from before the encrypted store. Run `tgmcp login` once: it moves the existing login into `secrets.bin` without a new code and removes the old files ([privacy.md](privacy.md) says what removal does not cover).

### "Telegram asks to wait N seconds before the next request"

Telegram's flood protection. Wait that long, then ask for less: a shorter period, a smaller `limit`, one chat instead of all.

### A search finds nothing, but the message exists

Telegram matches word beginnings only. Ask for other forms of the word, or a search inside one chat.

### "<tool> took longer than 30 seconds; try a smaller limit."

Each tool call has 30 seconds. Ask for fewer messages.

### Python 3.14 is not found

Install it from python.org with the "py launcher" option ticked, then repeat the install.

## Uninstall

1. End the login and remove `secrets.bin`:

   ```
   .venv\Scripts\tgmcp logout
   ```

2. Remove the server from Claude Code (or delete the `telegram` entry from your client's configuration):

   ```
   claude mcp remove telegram --scope user
   ```

3. Delete the data folder `%LOCALAPPDATA%\telegram-mcp` (or the folder in `TGMCP_HOME`), then the cloned folder.
4. In the Telegram app, open Settings, Devices and end the session if it is still listed.

The API key you created at https://my.telegram.org stays with your Telegram account; keep the `api_hash` private.
