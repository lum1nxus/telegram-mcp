# Roadmap

For anyone curious what may come next. What is worth doing next, with the trade-off of each item. Nothing here is promised. The read-only rule is not on the list and will not be: see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Local full-text index (SQLite FTS5)

Telegram's own search matches word beginnings only, so "market" does not find "supermarket", and several words are matched as separate prefixes. A local index built with SQLite FTS5 could search fragments and phrases, rank results and work offline. The price is the main promise of today's design: a copy of your chats on disk. If it ever comes, it should be opt-in, encrypted with the same per-user protection as `secrets.bin`, restricted to chosen chats, easy to delete, and it must respect hidden chats.

## A local broker process

Each client session starts its own `tgmcp serve` with its own connection on the same login key. That works from one IP address, but Telegram may reject several connections from one key in the future, or after a VPN switch. A small local broker process that owns the single connection and serves all sessions would remove the problem. The price is a long-running background process, a local channel to secure, and more moving parts for a non-programmer to debug. Only worth it if the duplicated-key error shows up in real use.

## Keychain and Secret Service for secrets

Today `secrets.bin` is encrypted with DPAPI on Windows and is a plain `0600` file elsewhere. macOS Keychain and the Linux Secret Service (libsecret) would give those systems real at-rest protection. The price is a new dependency (or a command-line bridge) per platform, a prompt on some setups, and headless Linux servers that have no keyring.

## Media download behind an explicit flag

Today a message with a photo or a file only says that one is there. Downloading could let the model read a PDF or an image. The price: disk copies of private files, large transfers, and a bigger attack surface (file names, file contents as prompt injection). If added: off by default, a size cap, a temporary folder that is cleaned, never for hidden chats.

## Folder (dialog filter) awareness

Telegram users group chats into folders ("Work", "Family"). Letting `list_chats` filter by folder and `search_messages` scope to one would make questions shorter and results more focused. The price: one more API call and the mapping of folder rules (included chats, excluded types) to dialogs, which Telegram defines in a fairly involved way.

## `whoami --devices`

List Telegram's active sessions (name, platform, last active) so a user can see this tool's device and end others. The price: one more Telegram request that reads account-security data, and care that the output never reveals more than the user needs. Ending sessions would be a write action and stays out.

## Refill global search after dropping hidden chats

A search across all chats asks Telegram for `limit` hits and then drops those from hidden chats, so a page can come back shorter than asked, with `truncated` set (which also tells, by count only, that hidden hits exist). Fetching one more chunk until the page is full would give the model full pages. The price: more requests per search and a loop that must stop on its own.

## Checks that need a live account

Three behaviours were reasoned from Telethon's code but not yet confirmed on a real account: reading the General topic of a forum (`getReplies` with message id 1; if Telegram refuses it, use the plain history filtered to topic 1), whether `list_topics` returns more than 100 topics when asked for 200 (if not, cap the tool at 100 or page through), and what Telegram answers to a per-chat search with an empty query and no sender (today it would surface as "Global search needs a query.", the wrong sentence for one chat). Each needs one call on a real account and, depending on the answer, a small fix.

## Local models: follow-up questions (`tgmcp ask --follow-up`)

`tgmcp ask` answers one question and forgets it. A `--follow-up` option could keep the last run's messages in a file and continue from them, so "and what did she say after that?" works. The price: a file that holds message text on disk (it would need the same protection as `secrets.bin` and a clear way to delete it), and a context that grows past what a small model can hold.

## Local models: a model as the bench's judge

`tgmcp bench` scores with fixed rules: the right tools, expected words in the answer, no invented ids or titles. A second model judging each answer could also catch wrong retellings and wrong dates. The price: the judge makes its own mistakes, results stop being repeatable, and a run takes twice as long. Rules stay the reference; a judge would be an extra column.

## Local models: verify the LM Studio path live

`tgmcp ask --backend lmstudio` and `tgmcp bench --backend lmstudio` are tested only against hand-written answers, and the LM Studio chat window with `mcp.json` only by reading LM Studio's documentation; the LM Studio path has not yet been verified live. One run of each on a real LM Studio would confirm the request shape, the tool-call format and the model list, and may need small fixes.

## Local models: LM Studio without editing `mcp.json`

LM Studio's API can attach an MCP server to a single request ("ephemeral MCP"), so `tgmcp ask --backend lmstudio` could let LM Studio run the tools itself, with no `mcp.json` edit. The price: LM Studio then talks to tgmcp outside tgmcp's own loop (no result cap, no step count, no bench comparability), and the API is young and may change.

## Chat titles in global-search hits

A search across all chats returns each hit with its chat id only, so a model must call `get_chat` to name the chat, and small models often skip that and write the id. Adding the chat title to each hit would save a step. The price: a few more bytes per hit within the same result budget, and care that a hidden chat's title can never appear.

## A seed-facts test for `next_before_id`

The paging questions of the bench rely on the seed giving `next_before_id` 71 for the first page of Hiking Club. A test that pins these seed facts (page sizes, cursors, the ids the questions name) would catch a seed change that silently breaks the bench's rules. Small and cheap; worth doing with the next change to the seed.

## Smaller clean-ups

`tgmcp login` and the gateway each have their own fallback ladder over the secrets file and the old clear-text files; one `stored_credentials()` in `secrets.py` would replace both. The idle guard wakes every minute to check the clock; one sleep until the deadline would do. The idle-guard tests use real short sleeps; an injectable sleep would make them independent of the wall clock.

## Packaging

`uv tool install git+https://github.com/lum1nxus/telegram-mcp` would replace the clone, venv and two `pip` lines with one command. Publishing to PyPI is not planned: the name `telegram-mcp` on PyPI belongs to an unrelated package, and a name that could be taken over by someone else is a supply-chain risk for a tool that holds your login. If a PyPI release ever happens, it needs a reserved name, trusted publishing and a README that says which package is the real one (see the warning in [SECURITY.md](../SECURITY.md)).

## Translating the search note

The note attached to every search result is English, and the model explains it to the user in the user's language. A translated note (or a shorter, language-neutral one) could help models that answer poorly from English hints. The price: more strings to keep in sync with tests, and a way to choose the language. Low priority.
