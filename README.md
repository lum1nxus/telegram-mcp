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

To use a model on your own computer instead of Claude, see "Local models (nothing leaves your computer)".

## What it is

`tgmcp` lets Claude Code look into your own Telegram account: list your chats, search messages and read a conversation. It runs on your computer, logs in as you (through your own Telegram API key) and answers only when Claude asks.

### What it cannot do

- It cannot send, edit, delete or forward messages, react, or mark anything as read.
- It cannot join, leave or pin chats.
- It cannot download media; a message with a photo or a file only shows that one is there.
- It keeps no copy of your chats on disk. Messages are fetched from Telegram when asked and forgotten afterwards.

### How read-only is enforced

Three layers. What they promise, and what they do not:

1. **At run time.** tgmcp talks to Telegram through `ReadOnlyClient`, a Telethon client that checks every request before it leaves the computer against a short list of reading requests (list dialogs, read history, search, read a channel's details and a forum's topics, plus what connecting needs). Every write that goes through the client is refused and nothing is sent: tgmcp's own calls, Telethon's helper methods and the methods of returned objects (a message's `reply` or `delete`). `tgmcp login` and `tgmcp logout` use a second client whose list holds only the sign-in requests and the log-out.
2. **In the source.** A test reads every module of the package and fails on write-shaped names (`send`, `delete`, `reply`, `download`, ...), on dynamic tricks that could hide a call (`eval`, `vars`, computed `getattr`, ...), and on any Telethon name inside `tgmcp/telegram/` that is not on an explicit list.
3. **The contract test** also sends the forbidden requests to the real client class (without a network) and checks that each one is refused, including the ones reached through a returned message (`reply`, `delete`, `mark as read`). Code inside this package that deliberately tries to get round the guard (replacing its list, forging a request) is caught by this test and by review, not by the guard itself.

Out of scope: a modified copy of tgmcp or a patched Telethon. You run the code you install, so install it only from this repository (see [SECURITY.md](SECURITY.md)).

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
   - Your phone number, with the country code, for example `+1 555 0100`.
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
| `search_messages` | `query` (not empty), `chat` (none: all chats), `topic` (none; needs `chat`), `sender` (none: a user id or @username from your dialog list, see below; needs `chat`), `since` (none, a date), `until` (none, a date), `limit` (30, 1 to 100) |
| `get_messages` | `chat`, `topic` (none), `since` (none, a date and time), `until` (none, a date and time, not included), `before_id` (none, at least 1), `limit` (50, 1 to 100) |
| `get_message_context` | `chat`, `message_id` (at least 1), `before` (10, 0 to 30), `after` (10, 0 to 30) |

Dates and times are UTC: `since` and `until` of `search_messages` are whole UTC days (both included), and a date and time without a time zone in `get_messages` is taken as UTC. If you live east of Greenwich, a message sent just after your local midnight belongs to the previous UTC day.

Answers are kept small: a message text is cut at 2 000 characters (`get_message_context` with `before=0, after=0` returns it in full), and a result stops at about 30 KB, with `truncated: true` and, for `get_messages`, a `next_before_id` to continue. `get_messages` also says `truncated` when it stopped early in a long run of service messages (joins, pins); the cursor then continues below them, and a search across all chats says `truncated` in the same case. `list_chats` and `list_topics` stop at about 25 KB too (narrow them with `query` or `kind`).

`sender` is reliable only for users of your dialog list (people you have a private chat with). A member of a large group or a channel is usually not found, even after their messages were read, because Telegram sends such users without the details tgmcp would need; the tool then says "Unknown sender", and searching without `sender` is the way round. tgmcp never looks a name up on Telegram's servers, because that lookup is one of the requests Telegram limits most strictly.

## Search semantics

Telegram search matches the beginnings of words, not fragments: "market" finds "marketing", but "ket" finds nothing. When a search finds nothing, try another form of the word before concluding that the message does not exist.

## Privacy and what the tool can see

- It sees everything your Telegram account sees (private chats, groups, channels, archived chats), except the chats you hide. What a tool returns goes to Claude as part of your conversation, like anything else you let Claude read.
- Forwarded messages name where they came from (`forwarded_from`), even when the source chat is hidden.
- A channel's comments live in its linked discussion group, which holds a copy of every post.
- Nothing leaves your PC except requests to Telegram's servers and the tool answers that go to Claude. There is no telemetry. Logs hold tool names, counts and error types, never message text, chat titles or what Claude asked for.
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
- `config.json`: the settings that are not secret: API id, hidden chats, the default local model (`local_model`).
- `tgmcp-<number>.log`: a technical log without message text, one file per running server (the number is its process id). Files older than 7 days are removed automatically.

When tgmcp creates the folder, it makes it readable only by your Windows user (a folder that already exists is left as it is), and `secrets.bin` is restricted the same way. If you think the login key leaked, run `.venv/Scripts/tgmcp logout`, or end the session in Telegram under Settings, Devices.

On systems other than Windows there is no encryption: `secrets.bin` is a plain file protected only by its `0600` mode, and `whoami` prints a warning.

If an administrator resets your Windows password (instead of you changing it yourself), Windows may no longer be able to decrypt `secrets.bin`. tgmcp then says so; run `.venv/Scripts/tgmcp login` again.

Versions before 21.10.2026 kept the hash in `config.json` and the login in `telegram.session`, both in clear text. If those files are still there, tgmcp says "Old clear-text files found"; run `.venv/Scripts/tgmcp login` once. It moves the existing login into `secrets.bin` (no new code from Telegram) and removes the old files. Removing them does not shred them: copies in backups, cloud sync or Windows shadow copies stay valid until you run `.venv/Scripts/tgmcp logout`.

## Local models (nothing leaves your computer)

Claude is a cloud model: what a tool returns (your messages) goes to Anthropic as part of the conversation. If you would rather keep your chats away from any AI company, tgmcp can work with a model that runs on your own computer instead. Your questions, the messages the tools read and the model's answers then stay on this PC. tgmcp itself still fetches the messages from Telegram's servers, as always.

The price: a local model is much smaller than Claude, so its answers are simpler and it makes more mistakes (see "Limits" below). You need a graphics card with about 8 to 12 GB of memory, or patience: on the processor alone it works, but slowly.

There are two ways. **LM Studio** is a program with a chat window, like the Claude app; it is the easiest. **Ollama with `tgmcp ask`** answers one question in a terminal; it is also how you measure which model works best (`tgmcp bench`). Both need `.venv/Scripts/tgmcp login` once before, as for Claude Code.

### LM Studio (a chat window)

1. Download LM Studio from https://lmstudio.ai and install it.
2. In LM Studio, search for one of the models from "Which model" below (for example "qwen3.5 9b"), download it and load it.
3. When you load the model, set the **context length to at least 8192** (16384 is better if it fits). A shorter context cuts off the tool answers.
4. Open the **Program** tab on the right, then **Install**, then **Edit mcp.json**. Paste this inside the `mcpServers` object (replace the path with your folder; keep the doubled backslashes):

   ```json
   "telegram": {
     "command": "C:\\path\\to\\telegram-mcp\\.venv\\Scripts\\tgmcp.exe",
     "args": ["serve"]
   }
   ```

   If `mcpServers` already holds other servers, put a comma after the neighbouring entry. Save the file.
5. In LM Studio's server settings (the **Developer** tab), keep **"Serve on local network" switched off**, so LM Studio's own server stays reachable from this computer only.
6. Start a new chat and ask. Before each tool call LM Studio shows a confirmation dialog with the tool name and its arguments; allow it. The dialog does not show what the tool returned; the model's answer is built from it.

### Ollama and `tgmcp ask` (one question in a terminal)

1. Install Ollama from https://ollama.com.
2. Download a model (about 6 GB): `ollama pull qwen3.5:9b`
3. In the repository folder, ask:
   - Git Bash or PowerShell: `.venv/Scripts/tgmcp ask "What did Nora say about the boots?"`
   - cmd.exe: `.venv\Scripts\tgmcp ask "What did Nora say about the boots?"`

Only the answer is printed. Useful options:

- `--model gemma4:12b`: another installed model. Without it tgmcp takes `local_model` from `config.json` (or the variable `TGMCP_LOCAL_MODEL`), else the first model Ollama lists. To make a model the default, add `"local_model": "qwen3.5:9b"` to `config.json` (inside the braces, with a comma after the entry before it, so the file stays valid JSON).
- `--show-calls`: also print each tool call and the size of its answer, for example `-> search_messages(query="boots") 2311 chars`. "cut" means tgmcp shortened a long answer before passing it to the model.
- `--backend lmstudio`: use LM Studio's server instead of Ollama (start it in LM Studio's Developer tab first). `--think` works with Ollama only.
- `--think`: let the model think before answering (slower, sometimes better).
- `--max-steps 8`: how many times the model may be called; when the limit is reached, the run stops unfinished.

If the model does not finish, tgmcp prints `(unfinished after N steps)` and what the model had found so far. Ask a narrower question (one chat, a shorter period).

### What "local only" checks, and what it cannot see

`tgmcp ask` and `tgmcp bench` refuse to talk to anything but this computer: the address must be `http://127.0.0.1`, `http://localhost` or `http://[::1]` (any port), system proxies are bypassed and a redirect to another address is refused. There is no option to turn this off. Ollama can also run "cloud" models that only look local (their names end in `-cloud`); tgmcp refuses those by name, and also when Ollama cannot tell the model's size and quantization, which a cloud model does not have. With `--backend lmstudio` the address check is the whole local-only rule: LM Studio's server does not say where a model runs, so tgmcp cannot check the model itself.

What tgmcp cannot see:

- Inside the **LM Studio window**, LM Studio is in charge, not tgmcp. Its own online features (searching and downloading models, updates, any cloud or remote feature it offers) are outside tgmcp's control. Check its settings yourself.
- Other software on your PC (a VPN, a program that reads your screen) is outside any tool's control.

### Which model works best: `tgmcp bench`

`tgmcp bench` asks a model 30 fixed questions about an **invented** account built into tgmcp (no login, none of your chats) and checks the answers with fixed rules. It takes a few minutes per model.

- Git Bash or PowerShell: `.venv/Scripts/tgmcp bench --model qwen3.5:9b`
- cmd.exe: `.venv\Scripts\tgmcp bench --model qwen3.5:9b`
- `--set smoke`: only five questions, for a quick check. `--repeat 3`: ask each question three times (small models do not always answer the same way). `--think`: with thinking. `--json log.json`: save every question, tool call and answer to a file.

How to read the table: one line per kind of question (finding a chat, reading history, paging back, reading around a message, forum topics, hidden chats, refusing to write, Polish and Russian questions), then `total`. `n` is the number of runs. The four percentages say how often the model called the right tools (`tools`), with the right arguments (`args`), wrote an answer that contains the expected facts (`answer`), and named nothing it had not seen in the question or a tool answer (`no_inv`, "no invention": it checks chat ids, message ids cited as ids, and the seed's chat titles; dates and other names are not checked). `steps` is the average number of tool calls, `s` the average seconds per question; the first question's time includes loading the model into memory. Higher percentages are better. The language of the answer is not scored. Compare models with the same `ctx` (the context length), `think` and `repeat` (all shown in the first line; with LM Studio, `ctx=app` means the length set in LM Studio).

### Which model

Candidates that fit a 12 GB graphics card (all in Ollama's library, 6 to 9 GB each):

| Model | Download | Published pointer |
|---|---|---|
| Qwen 3.5, 9 B | `ollama pull qwen3.5:9b` | BFCL-V4 (a tool-calling benchmark) 66.1, per https://vdf.ai/blog/best-local-llm-by-vram/ |
| Gemma 4, 12 B | `ollama pull gemma4:12b` | none collected |
| Ministral 3, 14 B | `ollama pull ministral-3:14b` | none collected |
| Qwen 3, 14 B | `ollama pull qwen3:14b` | none collected |

Published numbers come from other tests and are only a pointer. What decides is our own `tgmcp bench`: run it on the models that fit your computer and keep the one with the best `total` line.

### Limits

- One question per run: `tgmcp ask` does not remember the previous question, so put everything into one question.
- Answers are weaker than Claude's, most of all on long summaries of many messages.
- The bench's `no_inv` checks chat ids, message ids cited as ids, and the seed's chat titles; dates and other names are not checked, so a wrong date or a wrong retelling is caught only where a question's rules expect a specific fact.
- Telegram's search needs the word in the chat's language: a model asked in Russian about English messages must search with the English word, and small models do not always do that.

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
