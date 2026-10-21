# Security

tgmcp holds the login key of your Telegram account. Treat it like a password manager: small, read-only, and honest about its limits.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting (the "Security" tab of the repository, "Report a vulnerability"). Include what you saw, the steps to reproduce and your Windows and Python versions. Never include a real `secrets.bin`, session string or chat content. You will get an answer within a week.

In scope: anything that lets the server write to the account, leak the session or the API hash, write chat text to disk, or reach a network destination other than Telegram. Out of scope: the security of the Telegram account itself (use two-step verification and review Settings, Devices in the Telegram app).

## Install only from this repository

The name `telegram-mcp` on PyPI belongs to an unrelated package, and `pip install telegram-mcp` installs that package, not this one. Install tgmcp from a clone of this repository, as the README says, with the hashed lock files. Be suspicious of any other source that claims to be this tool.

## Threat model in one table

What the tool protects, against what. "Yes" means the design stops it; "No" means it does not, and the README says so.

| Threat | Protected? | Why, and what to do |
|---|---|---|
| Another Windows user on the same PC | Yes | The data folder and `secrets.bin` are restricted to your user (`icacls`), and `secrets.bin` is encrypted with DPAPI for your user. An administrator can still take over the PC and, with your password, may reach your data. |
| A stolen or copied `secrets.bin` file (backup, cloud sync, USB stick) | Mostly | The file is useless on another PC or under another user. It is not useless if the attacker also has your whole Windows profile (the DPAPI master keys) and your password, as in a full profile backup. Old clear-text files from before 21.10.2026 are not shredded when removed: run `tgmcp logout` to invalidate any copy. |
| A stolen laptop, disk encryption (BitLocker) on | Yes, in practice | The disk cannot be read without the BitLocker key; DPAPI adds the need for your Windows password. A weak or absent password, or a laptop that is stolen while you are logged in and unlocked, weakens this. |
| A stolen laptop, no disk encryption | Weak | An attacker can read the disk offline. The DPAPI master keys are protected by your Windows password, so a strong password helps and a weak one can be cracked. Turn on BitLocker. If the laptop is lost, end the session in Telegram, Settings, Devices. |
| Malware or any program running as your user | No | A program running as you can decrypt `secrets.bin` the same way tgmcp does, or read the chats directly. No operating-system store stops this, and the tool does not claim to. Keep the PC clean. |
| A message in a chat that tries to give Claude instructions (prompt injection) | Reduced, not solved | The tools only read, so injected text cannot send messages or change your account through tgmcp. Every tool tells Claude to treat message text as data. But Claude may still be misled into doing something with its other tools (files, shell, web) or into repeating text, so be careful when you let Claude act on chat content, and review the permission prompts. |
| Someone who can see your Claude conversation or the service that runs it | No | Whatever a tool returns becomes part of your conversation. Hide sensitive chats (`hidden_chats`) and ask only for what you need. |
| Telegram itself, or a network observer | Out of scope | The tool uses Telegram's encrypted protocol (MTProto) through Telethon; regular (non-secret) chats are readable by Telegram. Secret chats are not accessible to any client of this kind. |

## What the tool never does

- It never sends, edits, deletes, forwards or reacts, and it never marks anything as read. A runtime guard enforces it: tgmcp's Telethon client (`ReadOnlyClient`) refuses every request that is not on a short list of reading requests, before anything leaves the computer, whoever builds the request (including the methods of a returned message, such as `reply` or `delete`). The login client may additionally sign in and log out, nothing else. A contract test checks both lists at run time and reads the source for write-shaped names and dynamic lookups.
- It never writes message text to disk or to the log, and it has no telemetry.
- It never contacts any server except Telegram's.
- It never looks up a username on Telegram's servers for a `sender` filter: only people already seen in the session are accepted.

## Good habits

- Keep Windows updated, use a strong login password and turn on BitLocker.
- Hide the chats you do not want Claude to see (README, "Hidden chats"), and remember the forwarded-message and discussion-group caveats.
- If anything looks wrong, run `tgmcp logout` and end unknown sessions in Telegram, Settings, Devices.
