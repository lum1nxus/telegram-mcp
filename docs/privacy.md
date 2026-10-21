# Privacy and security

For anyone who wants to know what tgmcp can see, where it keeps things, how read-only is enforced, and what it does not protect against.

## What the model sees

tgmcp sees everything your Telegram account sees (private chats, groups, channels, archived chats), except the chats you hide. What a tool returns goes to the model as part of your conversation:

- with Claude Code, or any cloud client, it goes to that company's servers;
- with a local model ([local-models.md](local-models.md)), it stays on your PC.

Nothing else leaves the PC: tgmcp talks only to Telegram's servers and, for `tgmcp ask` and `tgmcp bench`, to a model server on this computer. There is no telemetry.

A chat can contain text that looks like instructions to the model. Every tool tells the model to treat message text as data, and the tools cannot write, so such text cannot change your account through tgmcp. It can still mislead a model that has other tools (files, shell, web): read the permission prompts of your client.

## Hidden chats

List the chats the model must not see in `config.json` under `hidden_chats`, by id, @username or exact title:

```json
"hidden_chats": ["-1001234567890", "@somechannel", "Family"]
```

Or set the variable `TGMCP_HIDDEN_CHATS` to a comma-separated list; it replaces the list in `config.json`, and an empty variable counts as not set. A hidden chat is not listed, cannot be opened by id, name or title, and its messages are dropped from searches across all chats. `tgmcp whoami` shows how many chats are hidden and whether each entry matched one.

What hiding does not cover:

- A message forwarded from a hidden chat into a visible one still shows, and its `forwarded_from` names where it came from.
- A channel's comments live in its linked discussion group, which holds a copy of every post. Hiding the channel does not hide that group: hide the group too (`get_chat` on the channel shows its `discussion_chat_id`).
- Hiding a private chat with a person does not hide that person's messages in groups.
- A search across all chats may come back shorter than asked when hits from hidden chats were dropped; the count is the only trace.

## Where things are stored

One folder: `%LOCALAPPDATA%\telegram-mcp` on Windows, `~/.local/share/telegram-mcp` elsewhere, or the folder in `TGMCP_HOME`. When tgmcp creates it, it restricts it to your user; a folder that already exists is left as it is.

| File | What it holds |
|---|---|
| `secrets.bin` | the API hash and the login key, encrypted (below) and restricted to your user |
| `config.json` | settings that are not secret: `api_id`, `hidden_chats`, `expected_user`, `local_model` |
| `tgmcp-<pid>.log` | a technical log, one file per running server; files older than 7 days are removed |

There is no `.session` file: the login is kept in memory and saved only inside `secrets.bin`. Nothing from your chats is written to disk. The log holds connection events, chat ids, counts and error types; never message text, chat titles or search words.

### Settings

Variables override `config.json`:

| Variable | `config.json` key | What it does |
|---|---|---|
| `TGMCP_HOME` | | the data folder |
| `TGMCP_API_ID` | `api_id` | the Telegram API id |
| `TGMCP_API_HASH` | | the API hash, for development; normally it lives only in `secrets.bin` |
| `TGMCP_HIDDEN_CHATS` | `hidden_chats` | chats to hide |
| `TGMCP_EXPECTED_USER` | `expected_user` | a username or user id; the server refuses to start a connection if the login belongs to another account |
| `TGMCP_LOCAL_MODEL` | `local_model` | the default model of `tgmcp ask` |

Keep `config.json` valid JSON when you edit it: entries inside the braces, separated by commas.

### Encryption and its limits

On Windows, `secrets.bin` is encrypted with DPAPI for your Windows user. A copy is useless on another PC or under another user, unless the attacker also has your Windows profile's DPAPI master keys and your password (a full profile backup). It still opens for any program running as you.

If an administrator resets your Windows password (instead of you changing it), Windows may not decrypt `secrets.bin` any more; run `tgmcp login` again.

On other systems there is no encryption: `secrets.bin` is a plain file protected by its `0600` mode, and `tgmcp whoami` prints a warning.

If you think the login key leaked, run `tgmcp logout`, or end the session in Telegram under Settings, Devices.

### Old clear-text files

Versions before 21.10.2026 kept the API hash in `config.json` and the login in `telegram.session`, in clear text. `tgmcp login` moves them into `secrets.bin` and removes the old files. Removal does not shred them: copies in backups, cloud sync or shadow copies stay valid until you run `tgmcp logout`.

## How read-only is enforced

**At run time.** The server talks to Telegram through `ReadOnlyClient`, which checks every request before it leaves the computer against a short list of reading requests (list dialogs, read history, search, read a channel's details and a forum's topics, plus what connecting needs). Anything else is refused and nothing is sent: tgmcp's own calls, Telethon's helper methods, and the methods of returned objects (a message's `reply` or `delete`). `tgmcp login` and `tgmcp logout` use a second client whose list adds only the sign-in requests and the log-out.

**In the source.** A test reads every module of the package and fails on write-shaped names (`send`, `delete`, `reply`, `download`, ...), on dynamic tricks that could hide a call (`eval`, `vars`, computed `getattr`, ...), and on any Telethon name inside `tgmcp/telegram/` that is not on an explicit list. The same test sends the forbidden requests to the real client class, without a network, and checks that each one is refused.

Out of scope: a modified copy of tgmcp or a patched Telethon. You run the code you install, so install it only from this repository ([SECURITY.md](../SECURITY.md)).

## Several sessions and VPN

Each client session starts its own `tgmcp serve` with its own connection on the same login. Telegram rejects the same login from two IP addresses at once, so switching a VPN in the middle of a session can break it. Then run `tgmcp logout` and `tgmcp login`.

## Local models

`tgmcp ask` and `tgmcp bench` talk only to this computer:

- The address must be `http`, with the host `127.0.0.1`, `localhost` or `::1` (any port). Anything after the port is dropped.
- System proxies are bypassed, and a redirect is refused.
- With Ollama, a model whose name ends in `-cloud` is refused, and so is a model that Ollama marks with a remote host or remote model, or for which it does not report a parameter size and quantization.
- There is no option to turn this off.

With LM Studio, the address check is the whole rule: LM Studio's server does not say where a model runs.

What tgmcp cannot see:

- Inside the LM Studio window, LM Studio is in charge: its online features (model search and downloads, updates, any remote feature) are governed by its own settings.
- The Ollama app also has online features (updates, sign-in to ollama.com, cloud models in its list). tgmcp refuses cloud models only in its own runs.
- Other software on your PC (a VPN, a screen reader) is outside any tool's control.

## Threat model

"Yes" means the design stops it; "No" means it does not.

| Threat | Protected? | Why, and what to do |
|---|---|---|
| Another Windows user on the same PC | Yes | The folder and `secrets.bin` are restricted to your user, and `secrets.bin` is encrypted with DPAPI for your user. An administrator can still take over the PC and, with your password, may reach your data. |
| A copied `secrets.bin` (backup, cloud sync, USB stick) | Mostly | Useless on another PC or under another user, unless the attacker also has your whole Windows profile and your password. |
| A stolen laptop with BitLocker on | Yes, in practice | The disk cannot be read without the BitLocker key; DPAPI adds the need for your Windows password. A weak password, or a laptop stolen while unlocked, weakens this. |
| A stolen laptop without disk encryption | Weak | The disk can be read offline; the DPAPI keys are protected by your Windows password, which a weak one does not hold. Turn on BitLocker; if the laptop is lost, end the session in Telegram, Settings, Devices. |
| Malware or any program running as your user | No | It can decrypt `secrets.bin` the same way tgmcp does, or read the chats directly. Keep the PC clean. |
| A chat message that tries to give the model instructions | Reduced, not solved | It cannot write through tgmcp, but it can mislead a model that has other tools. Review your client's permission prompts. |
| Someone who can see your conversation with a cloud model | No | Whatever a tool returns becomes part of the conversation. Hide sensitive chats, or use a local model. |
| Telegram itself, or a network observer | Out of scope | Telethon uses Telegram's encrypted protocol (MTProto); regular chats are readable by Telegram. Secret chats are not reachable by a client of this kind. |
