# Roadmap

What is worth doing next, with the trade-off of each item. Nothing here is promised. The read-only rule is not on the list and will not be: see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Local full-text index (SQLite FTS5)

Telegram's own search matches word beginnings only, so "market" does not find "supermarket", and several words are matched as separate prefixes. A local index built with SQLite FTS5 could search fragments and phrases, rank results and work offline. The price is the main promise of today's design: a copy of your chats on disk. If it ever comes, it should be opt-in, encrypted with the same per-user protection as `secrets.bin`, restricted to chosen chats, easy to delete, and it must respect hidden chats.

## A local broker process

Each Claude session starts its own `tgmcp serve` with its own connection on the same login key. That works from one IP address, but Telegram may reject several connections from one key in the future, or after a VPN switch. A small local broker process that owns the single connection and serves all sessions would remove the problem. The price is a long-running background process, a local channel to secure, and more moving parts for a non-programmer to debug. Only worth it if the duplicated-key error shows up in real use.

## Keychain and Secret Service for secrets

Today `secrets.bin` is encrypted with DPAPI on Windows and is a plain `0600` file elsewhere. macOS Keychain and the Linux Secret Service (libsecret) would give those systems real at-rest protection. The price is a new dependency (or a command-line bridge) per platform, a prompt on some setups, and headless Linux servers that have no keyring.

## Media download behind an explicit flag

Today a message with a photo or a file only says that one is there. Downloading could let Claude read a PDF or an image. The price: disk copies of private files, large transfers, and a bigger attack surface (file names, file contents as prompt injection). If added: off by default, a size cap, a temporary folder that is cleaned, never for hidden chats.

## Folder (dialog filter) awareness

Telegram users group chats into folders ("Work", "Family"). Letting `list_chats` filter by folder and `search_messages` scope to one would make questions shorter and results more focused. The price: one more API call and the mapping of folder rules (included chats, excluded types) to dialogs, which Telegram defines in a fairly involved way.

## A pagination cursor after the service-message safety stop

A page of history skips service messages (joins, pins) and gives up after a fixed number of raw messages, so a long run of them cannot keep it fetching. The page can then end short, and the cursor to continue is only given for a full page. Returning a cursor in this case too would let Claude page through such chats. The price is small: a cursor that is correct when the stop happens inside a run of service messages, and tests for it.

## `whoami --devices`

List Telegram's active sessions (name, platform, last active) so a user can see this tool's device and end others. The price: one more Telegram request that reads account-security data, and care that the output never reveals more than the user needs. Ending sessions would be a write action and stays out.

## CI on `windows-latest`

CI runs on Linux, where DPAPI and `icacls` are replaced by stubs. A Windows job would run the real DPAPI round trip and the real folder-permission code on every change. The price: longer CI runs and Windows runner quirks (profile folders, `icacls` output). Worth it because that code protects the secrets.

## Packaging

`uv tool install git+https://github.com/lum1nxus/telegram-mcp` would replace the clone, venv and two `pip` lines with one command. Publishing to PyPI is not planned: the name `telegram-mcp` on PyPI belongs to an unrelated package, and a name that could be taken over by someone else is a supply-chain risk for a tool that holds your login. If a PyPI release ever happens, it needs a reserved name, trusted publishing and a README that says which package is the real one (see the warning in [SECURITY.md](../SECURITY.md)).

## Translating the search note

The note attached to every search result is English, and Claude explains it to the user in the user's language. A translated note (or a shorter, language-neutral one) could help models that answer poorly from English hints. The price: more strings to keep in sync with tests, and a way to choose the language. Low priority.
