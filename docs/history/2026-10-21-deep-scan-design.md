# Deep scan: `scan_chat`, in-memory search over one chat's recent history

Design, 21.10.2026. Status: approved by the author in chat (21.10.2026, "да и да": deep scan
now, an optional encrypted index as the next step).
Section 9 records the rulings that changed sections 3–6a during the build; where they differ, section 9 wins.

## 1. Intent

Telegram's search matches word beginnings only, and even the opt-in expansion cascade
cannot find a fragment inside a word, a phrase, a pattern, or a word with a typo. Every
existing tool that searches better keeps a copy of the chats on disk. `scan_chat` is the
middle path that keeps tgmcp's promise: the server reads the last N thousand messages of
**one** chat into memory, matches them with its own algorithms, returns the hits with an
honest statement of what was scanned, and forgets everything. Nothing is written to disk and
nothing is cached (an in-memory cache is a separate roadmap item).

## 2. Goals, non-goals, anti-goals

Goals

- Find messages by a fragment inside a word ("tractor" inside "minitractor"), a phrase, a
  regular expression, or a word with up to two typos, in one chat, within the last
  `max_messages` messages (default 3 000, ceiling 20 000) or since a date.
- Say exactly what was covered: how many messages were scanned, the oldest one's date, whether
  the start of the chat was reached, how long it took.
- Stay inside Telegram's good manners: pages of 100 through the existing gateway, a bounded
  number of pages, flood waits respected, the scan returns what it has when interrupted.
- One pure matching module reused later by the index.

Non-goals

- No scan across all chats (a chat is the unit; the model can call it twice).
- No semantic search, no synonyms, no local-model calls inside the server.
- No caching between calls; no disk.

Anti-goals

- Never more than `max_messages` raw messages fetched per call, never more than the ceiling,
  never a silent partial result (the header always says what was covered).
- Never a regular expression that can hang the server: the pattern is compiled with a size
  cap and a step budget (see §5).
- Hidden chats stay hidden; `topic`, `sender`, dates apply as in the other tools.

## 3. The tool

`scan_chat(chat: str, query: str, mode: "fragment" | "words" | "regex" = "fragment",
tolerance: int = 0 (0–2), topic: int | None = None, sender: str | None = None,
since: datetime | None = None, until: datetime | None = None, max_messages: int = 3000
(100–20 000), limit: int = 30 (1–100)) -> ScanResult`

Modes

- `fragment`: case-insensitive, diacritics-folded (NFD, ł→l, ё→е) substring match of the
  whole query against the folded text.
- `words`: every query word must appear as a whole word (same folding); word order free.
- `regex`: Python `re` with `IGNORECASE`; the query is the pattern; the folded text is matched.
- `tolerance` applies to `fragment` and `words`: 1 or 2 allows that many single-character
  edits per query word, implemented as a trigram similarity gate (Jaccard on character
  trigrams of the folded query word vs each text word) followed by a Levenshtein check on the
  candidates, so typos ("tracor" → "tractor") and transliteration slips are caught without
  scanning every pair with Levenshtein.

`ScanResult(chat: Chat, query: str, mode: str, tolerance: int, scanned: int,
oldest_scanned: datetime | None, newest_scanned: datetime | None, reached_start: bool,
stopped_by: "max_messages" | "since" | "start" | "time" | "flood" | "limit", elapsed_s:
float, hits: list[Message], truncated: bool, note: str)`

`hits` newest first, capped by `limit` and the result budget (`fit_budget`); `truncated`
when either cut; `note` restates coverage in one sentence the model can quote ("Scanned the
last 3 000 messages of Garden Club back to 2026-06-01; the chat continues further back.").

## 4. How it runs

1. Resolve the chat through `Directory` (hidden chats invisible as everywhere).
2. Page through `gateway.history(chat_id, topic=…, before_id=cursor, since=since,
   until=until, limit=100)` until `scanned >= max_messages`, the page is short (start of the
   chat or `since` reached), the time budget is spent, or `Busy` arrives. Each page's messages
   are matched immediately and dropped; only hits are kept (so memory is bounded by hits, not
   by the scan).
3. Time budget: `SCAN_TIMEOUT_S = 60` for the whole tool call (own ceiling, above the 30 s of
   the other tools); the loop checks the clock before every page and stops with
   `stopped_by="time"` when the next page would not fit (estimated from the mean page time).
4. `Busy` after at least one page → return what was scanned with `stopped_by="flood"` and the
   Telegram sentence in `note`; `Busy` on the first page → the existing `ToolError`.
5. Early exit `stopped_by="limit"`: when `limit` hits are already collected AND the caller
   gave no `since`, the scan may stop early — no: the header must be honest about coverage,
   and the model expects the newest `limit` hits; since pages run newest-first, the first
   `limit` hits are the newest ones, so stopping early is correct and saves requests. The
   header then says `scanned` and `oldest_scanned` for the pages actually read.

## 5. Safety of the matching module (`tgmcp/match.py`, pure)

- `fold(text) -> str`: lower-case, NFD + strip combining marks, ł→l, ё→е; cached per message
  during a scan.
- `fragment(query, text) -> bool`, `words(query, text) -> bool`, `regex(pattern, text) ->
  bool`, `fuzzy_words(query, text, tolerance) -> bool`; `trigrams(word) -> frozenset[str]`,
  `levenshtein(a, b, max_distance) -> int` with early exit.
- Regex safety: the pattern is limited to 200 characters, compiled once, and executed through
  `re` with a guard against catastrophic patterns: nested quantifiers `(…+)+`, `(…*)*` and
  back-references are refused with `ToolError("That pattern could run forever; simplify
  it.")`; a text is matched with `pattern.search` on at most 4 096 characters (the stored
  text cap). This is a heuristic, documented as such.
- All matching functions are deterministic and have no I/O.

## 6. Testing

- `tests/test_match.py`: fold table (Polish, Cyrillic ё, uppercase), fragment inside a
  compound, words in any order, regex with IGNORECASE, the refused patterns, tolerance 1 and 2
  with typos and with a false positive that must not match, trigram/Levenshtein edge cases
  (short words, empty query).
- `tests/test_tools_scan.py` through the real server and the fake: the Garden Club seed gains
  nothing (the fake's history already pages); tests: fragment finds "минитрактор" by
  "трактор" (which the exact search cannot), `max_messages` stops the scan and the header says
  so, `reached_start` on a small chat, `since` stop, `topic`/`sender` filters applied, hidden
  chat invisible, `Busy` after a page → partial with `stopped_by="flood"`, early stop on
  `limit`, the time budget (monkeypatched clock), the note sentence, `truncated` by budget.
- `tests/test_gateway_client.py`: nothing new needed (the scan uses `history` as is).
- Bench: category `scan` with 4 questions (fragment, phrase, typo, regex) over the seed; the
  `IDEAL` runs; attainability test.
- Read-only contract: `scan_chat` reads only (`history`); names avoid the denylist.

## 6a. Rulings after the builder check (21.10.2026)

1. `sender`: a numeric id, or `@username` resolved through `Directory` (user dialogs that
   carry a username); anything else → the search tools' "Unknown sender" sentence. The filter
   is applied in memory after each page. `max_messages` and `scanned` count every message
   read (coverage of the chat); only `hits` are the sender's. No gateway change.
2. `max_messages` and `scanned` count the plain messages `history` returns; the gateway may
   read up to 4× as many raw Telegram messages (service messages are skipped). Accepted; the
   anti-goal reads "never more than `max_messages` messages returned by the gateway".
3. `tolerance` compares each query word with whole text words (trigram gate, then
   Levenshtein). In `fragment` mode with `tolerance > 0` a query word matches a text word when
   it is a fuzzy match of the whole word OR an exact substring of it; phrase adjacency is not
   required once `tolerance > 0` (it behaves like `words`). A typo inside a compound
   ("tracor" in "minitractor") is not caught; documented.
4. `regex` matches the ORIGINAL text with `re.IGNORECASE` (no folding: folding a pattern
   would break escapes and classes). Documented in the tool description.
5. The early stop on `limit` always applies (pages run newest-first, so the first `limit`
   hits are the newest). With `since`, a short page reports `stopped_by="since"` when the
   oldest scanned message is at or after `since`, else `"start"`.
6. Tolerance is clamped by query-word length: ≤ 3 letters → 0, 4–6 letters → at most 1,
   7+ → as given.
7. Pages are spaced by a fixed `PAGE_PAUSE_S = 0.25` after the first page; the ceiling stays
   20 000, which the 60 s budget cuts in practice (reported as `stopped_by="time"`).

## 7. Calls made here

- A new tool rather than a mode of `search_messages`: different cost, different honesty
  header, different arguments.
- Page size 100 through the existing `history` (no new gateway method).
- `SCAN_TIMEOUT_S = 60`, `max_messages` default 3 000, ceiling 20 000, pages of 100.
- Trigram gate + Levenshtein for tolerance; no external library.
- Regex heuristics instead of a full safe-regex engine.

## 8. Left to the builder

- The exact page-time estimate for the time budget.
- Wording of `note` for each `stopped_by`.
- The four bench questions and their rules.

## 9. Rulings during the build (21.10.2026)

- `fold` uses an explicit accent table, not NFD: й must stay й.
- `regex` matches the original text (section 3 corrected).
- There is no step budget: the guard is a heuristic plus a 1024-character cap for patterns with two or more unbounded quantifiers.
- No fold cache.
- A bad `sender` format is refused with "sender must be a user id or @username."
- A numeric `sender` is used as is.
- The soft time budget is 50 s, the hard ceiling 60 s.
- The distance of "trackor" and "tractor" is 1 (the plan's example was wrong).
- The scan ends on `next_before_id is None` or on a short page that was not stopped early.
- Under `until` or `topic` the note carries a scope clause (" in topic 7 up to 2026-10-01") instead of claiming the whole chat.
