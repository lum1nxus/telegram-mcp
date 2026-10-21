# Roadmap

For anyone curious what may come next: ideas worth doing, each with its trade-off, none of them promised; the read-only rule is not up for change (see [CONTRIBUTING.md](../CONTRIBUTING.md)). The first two sections are the author's priorities; the rest is unordered.

## 1. Smarter search

Telegram's own search matches the beginnings of words: "market" finds "marketing" but not "supermarket", and two words are matched as two separate prefixes. Everything tgmcp finds today goes through that search, because it keeps no copy of your chats. Step 1 is done; the recommended order for the rest is: model-made synonyms, then a deep scan of one chat in memory, then steps 3 and 4 if they are still needed. Step 2 is better skipped.

### Step 1: query expansion, no index (done, 21.10.2026)

`search_messages` sends a short series of Telegram queries (stems, the other script, Polish without diacritics, common compounds such as "мини" + "трактор") and reports them in `queries_tried`; 6 queries at most in one chat, 4 across all chats. How it works: [tools.md](tools.md#search).

### Next: synonyms made by the local model

Before the search, the host asks the local model for two or three synonyms or translations of the key word ("теплица": "greenhouse", "парник") and searches each with the same cascade. Text stays on the PC. The price: one extra local model call per search (seconds on a 12 GB card), more Telegram requests, and synonyms that drift off the meaning; it helps only `tgmcp ask`, not other MCP clients, which make their own rephrasings.

### Next: deep scan of one chat in memory

For one named chat, the server reads its last few thousand messages into memory and matches them itself with trigrams or a regular expression, so a fragment ("трактор" inside "минитрактор", "traktor" inside "minitraktor") is found where Telegram's prefix search cannot. Nothing is written to disk and the copy is gone when the call ends; never across all chats, never for hidden chats. The price: time (a few thousand messages are tens of Telegram requests, seconds to a minute), flood waits on large chats, and a cap on how far back it looks.

### Step 2: splitting compound words

A morphological dictionary (pymorphy3 for Russian, Morfeusz for Polish) can split "минитрактор" into "мини" + "трактор" and recognise word forms a stemmer misses. On its own it buys little: Telegram still matches prefixes, and step 1 already tries common compound heads; the split only helps once there is a local index (step 3). The dictionaries weigh tens of megabytes. Skip unless step 3 lands.

### Step 3: a local full-text index (SQLite FTS5)

Opt-in: download the text of chosen chats into an encrypted database on disk and search it with a trigram tokenizer (fragments: "traktor" inside "minitraktor"), stemming, phrase search and relevance ranking; instant and offline. The price is the main promise of today's design: a copy of chats on disk. If it comes, it must be opt-in per chat, encrypted with the same per-user protection as `secrets.bin`, easy to delete, respect hidden chats, and warn that the first load of a large chat takes minutes and counts against Telegram's limits.

### Step 4: semantic search over the index

On top of step 3: embeddings from a local model (through Ollama) so "what did people recommend for growing tomatoes" finds messages about greenhouses with no word in common, across the three languages at once. The price: step 3 plus an embedding model (about half a gigabyte) and hours of indexing on a large account.

## 2. Final audit before calling it 1.0

Three questions answered by one deep review (Fable, with the full white-box report in the repository):

- **Security of the code as a whole**, again, after all the changes since the last review: the request allowlist, the contract test, secrets at rest, the local-only rule, prompt injection through chat content, what a malicious fork of this repository could do to a user who installs it.
- **Python best practices**: dead code, duplication, typing, asyncio hygiene, module sizes, test brittleness, packaging.
- **That it really is MCP**: conformance with the MCP specification as a server (initialize, tool listing, tool annotations, structured output, error results, cancellation, logging), checked with the MCP Inspector and the SDK's conformance tests, and interoperability with at least two clients besides Claude Code.

Alongside it, the repository's own protection is kept at: actions pinned to commit SHAs and restricted to GitHub-owned and verified publishers, a code-scanning merge gate, tag protection, secret scanning with push protection, Dependabot, no deploy keys or secrets, and two-factor authentication on the owner's account.

## Local full-text index (SQLite FTS5)

See step 3 above.

## A local broker process

Each client session starts its own `tgmcp serve` with its own connection on the same login key. That works from one IP address, but Telegram may reject several connections from one key in the future, or after a VPN switch. A small local broker process that owns the single connection and serves all sessions would remove the problem. The price is a long-running background process, a local channel to secure, and more moving parts for a non-programmer to debug. Only worth it if the duplicated-key error shows up in real use.

## Keychain and Secret Service for secrets

Today `secrets.bin` is encrypted with DPAPI on Windows and is a plain `0600` file elsewhere. macOS Keychain and the Linux Secret Service (libsecret) would give those systems real at-rest protection. The price is a new dependency (or a command-line bridge) per platform, a prompt on some setups, and headless Linux servers that have no keyring.

## Re-restrict an existing data folder

`ensure_home` restricts the data folder only when it creates it. A folder created by an older build (before the ACL fix) keeps its wider permissions until the user runs `tgmcp logout` and `login`. Restricting an existing folder once at every `login` would close that quietly. Small.

## Who said what, and period digests

Two question shapes small models handle badly today: "who said X" (they page through history and count by hand) and "what happened last week in group Y" (the full history blows the result budget). A `sender` summary on search results, and a `get_messages` mode that groups by day with counts and the first line of each message, would answer both within the budget. The price: two more result shapes to document and test.

## Search in the chat's language

A Russian question about a Polish chat fails because Telegram wants the word in the chat's language. The host could detect a chat's language from its last messages and translate the search term with the local model before calling the tool. Text stays on the PC. The price: one extra local model call per search and a wrong guess on mixed-language chats.

## Compress long results with the local model

The host cuts a tool result at 16 000 characters. Instead, a small local model could summarise the cut part so the main model loses less. Text stays on the PC. The price: a second model in memory, slower answers, and summaries that can drop the fact the user needed.

## Saved questions as MCP prompts

MCP lets a server offer ready-made prompts ("summarise last week in chat X"). A short list of them would turn a long question into one click in clients that show prompts. The price: strings to keep in sync with the tools, and clients differ in how they show prompts.

## In-memory result cache per session

Repeated calls for the same page (small models retry a lot) go to Telegram every time and count against flood limits. A cache in the server process, keyed by tool and arguments, for the life of the session, would cut that. Never on disk. The price: stale results within a session; a short time-to-live fixes it.

## `tgmcp ask --budget`

A wall-clock budget ("--budget 60s") so a slow model cannot hang a terminal for minutes; today only the 120 s per-request timeout and `--max-steps` bound a run.

## Media download behind an explicit flag

Today a message with a photo or a file only says that one is there. Downloading could let the model read a PDF or an image. The price: disk copies of private files, large transfers, and a bigger attack surface (file names, file contents as prompt injection). If added: off by default, a size cap, a temporary folder that is cleaned, never for hidden chats.

## Folder (dialog filter) awareness

Telegram users group chats into folders ("Work", "Family"). Letting `list_chats` filter by folder and `search_messages` scope to one would make questions shorter and results more focused. The price: one more API call and the mapping of folder rules (included chats, excluded types) to dialogs, which Telegram defines in a fairly involved way.

## `whoami --devices`

List Telegram's active sessions (name, platform, last active) so a user can see this tool's device and end others. The price: one more Telegram request that reads account-security data, and care that the output never reveals more than the user needs. Ending sessions would be a write action and stays out.

## Refill global search after dropping hidden chats

A search across all chats asks Telegram for `limit` hits and then drops those from hidden chats, so a page can come back shorter than asked, with `truncated` set (which also tells, by count only, that hidden hits exist). Fetching one more chunk until the page is full would give the model full pages. The price: more requests per search and a loop that must stop on its own.

## Checks that need a live account

Four behaviours were reasoned from Telethon's code or assumed but not yet confirmed on a real account: reading the General topic of a forum (`getReplies` with message id 1; if Telegram refuses it, use the plain history filtered to topic 1), whether `list_topics` returns more than 100 topics when asked for 200 (if not, cap the tool at 100 or page through), what Telegram answers to a per-chat search with an empty query and no sender (today it would surface as "Global search needs a query.", the wrong sentence for one chat), and whether Telegram's search ignores Polish diacritics and "ł" ("odmowila" finding "odmówiła"; the bench's fake account assumes it does, and if Telegram does not, the Polish search question measures only the fake). Each needs one call on a real account and, depending on the answer, a small fix.

## Local models: follow-up questions (`tgmcp ask --follow-up`)

`tgmcp ask` answers one question and forgets it. A `--follow-up` option could keep the last run's messages in a file and continue from them, so "and what did she say after that?" works. The price: a file that holds message text on disk (it would need the same protection as `secrets.bin` and a clear way to delete it), and a context that grows past what a small model can hold.

## Local models: a model as the bench's judge, language and stability columns

`tgmcp bench` scores with fixed rules: the right tools, expected words in the answer, no invented ids or titles. Two cheap columns first: the answer's language (today an answer in French to an English question scores 100 %) and the spread between `--repeat` runs. Then, optionally, a second model judging each answer for wrong retellings and dates. The price of a judge: it makes its own mistakes, results stop being repeatable, and a run takes twice as long. Rules stay the reference; a judge would be an extra column.

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
