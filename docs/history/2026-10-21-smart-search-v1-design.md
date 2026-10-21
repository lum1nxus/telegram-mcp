# Smart search v1: query expansion and compound words

Design, 21.10.2026. Status: approved by the author in chat (21.10.2026): "do item 1" of the
smarter-search proposal, i.e. query expansion with stems, spelling variants, a search
cascade and generated compound words. No local index, no model-made synonyms, no deep scan
(those stay in `docs/ROADMAP.md`).

## 1. Intent

Telegram's server search splits a message on spaces and punctuation and matches the
beginning of each word; it never matches a fragment. Today one tool call makes one Telegram
search, so "фруктовая компания" finds nothing when the chats say "фруктом" or "frukt".
The server should do the thinking a careful person would do by hand: try the word stems,
the other spelling, the pairs and single words, and the likely compounds, then merge what
came back and say what it tried. The model keeps calling `search_messages` once.

## 2. Goals, non-goals, anti-goals

Goals

- `search_messages` finds messages where the query words appear in another grammatical
  form, another script (Latin/Cyrillic), without diacritics, or as the tail of a common
  compound ("минитрактор"), in Russian, Polish and English.
- The result says which queries were tried (`queries_tried`) and how many hits each gave,
  so "nothing found" stays an honest statement about what was looked for.
- At most 6 Telegram searches per tool call; the cascade stops as soon as a step finds
  something, so the common case still costs one request.
- `expand=False` restores today's single exact search (for the bench and for users who
  want the raw behaviour).

Non-goals

- No synonyms, no translation, no model calls inside the server.
- No copy of chats, no cache on disk (an in-memory cache is a separate roadmap item).
- No change to the other five tools.

Anti-goals

- Never more than `MAX_QUERIES` requests per call, whatever the query looks like.
- Never return a hit twice; never exceed `limit`; never silently widen a search the user
  scoped (`chat`, `topic`, `sender`, dates apply to every expanded query).
- Hidden chats stay hidden on every path, as today.

## 3. The algorithm (`tgmcp/expand.py`, pure functions, no I/O)

1. **Tokenise** the query: split on whitespace and punctuation except `@` and `#`; keep
   tokens of 2+ characters; drop stop words (a short fixed list per language: и, в, на, про,
   the, a, of, w, na, o, …). Detect each token's script (Cyrillic / Latin).
2. **Prefix** each token. Snowball stems are not reliable word beginnings ("фруктовая" →
   "фруктов", which is not a prefix of "фруктом"; "company" → "compani"), so the stem is only
   a hint: the search prefix is the longest common prefix of the token and its Snowball stem
   (Russian for Cyrillic tokens; for Latin tokens the shorter of the English and Polish
   stems), with a floor of 4 characters (below it the token itself is used). If that prefix
   ends with a derivational tail from a small table (Russian: ов, ев, ск, ист, изм;
   Polish: ow, sk — the single letters н, к, n were dropped during implementation: they cut
   "компан" to "компа" and "сантехник" to "сантехни") and stripping it leaves 5+ characters, strip it: "фруктов" → "фрукт".
   Measured on the real stemmer: фруктовая/фруктом/фрукт → "фрукт"; садовая → "садов"
   (cutting "ов" would leave 3 letters); компания/компании → "компан"; planting/plants →
   "plant"; hydraulikiem → "hydraulik"; odmówiła → "odmów"; mowing → "mowing" (the English
   stem "mow" is under 4 letters, so the token stays).
3. **Variants** per prefix, in this order, deduplicated, at most 3: the other script (a
   fixed transliteration table both ways: трактор ↔ traktor); the diacritics-stripped form
   for Polish (ą ć ę ł ń ó ś ź ż → a c e l n o s z z; one direction only — the reverse has
   too many candidates); ё → е. Assumption to verify live: Telegram folds diacritics when
   matching; the fake gateway folds them on both sides so the Polish bench question is
   attainable. If live Telegram turns out not to fold, the stripped variant simply finds
   nothing extra.
4. **Compounds** (only when the cascade below reaches step c with zero hits): for each stem
   of 4+ characters, prefix it with each entry of a fixed list of common compound heads per
   script (Russian: авто, микро, мини, макро, супер, бизнес, гидро, суб, интер, евро,
   фото, видео, теле, электро, энерго, агро, эко, био, нано, мульти; Latin: auto, micro,
   mini, super, sub, inter, euro, eco, bio, e, foto, video, tele, multi). Limit 20
   compounds per stem; the cascade packs them into one Telegram query each is not possible
   (Telegram ANDs words), so compounds are tried one by one within the request budget.
5. **Cascade**: queries are built in this order and sent within the budget of
   `MAX_QUERIES = 6` (4 for a global search):
   a. all prefixes joined (the exact-ish query);
   b. if 2+ tokens: the rarest pair joined;
   c. the single rarest prefix;
   d. the rarest prefix's variants, one query each (other script, diacritics-stripped, ё→е);
   e. compounds of the rarest prefix, most common heads first.
   Steps a–d: one query each; the first of them with (visible) hits ends the cascade. Step e
   runs only if a–d found nothing and keeps trying compounds within the remaining budget,
   merging every hit (compounds are different words, so their hits are disjoint and all
   wanted). A query identical to an earlier one is skipped without spending budget.
   Rarity ranking of tokens: not a stop word; contains a digit or `@`; capitalised and not
   the first token; longer; earlier position as the tie-break.
6. **Merge**: union of hits keyed by `(chat_id, id)`, newest first, cut to `limit`;
   `truncated` is true if any step returned a full page or the union exceeded `limit`.

## 4. The tool

`search_messages(query, chat?, topic?, sender?, since?, until?, limit=30, expand=True)`.
`SearchResult` gains `queries_tried: list[QueryTried]` with `QueryTried(query: str,
hits: int)` in cascade order (an empty list when `expand=False`), and the `note` says, when
`queries_tried` is non-empty and `hits` is empty: "Nothing found for any of the
N queries tried; see queries_tried." The tool description gains one sentence: the server
tries word stems, spellings and common compounds; pass `expand=False` for one exact search.
`search_in_chat` / `search_global` of the Gateway are unchanged; the expansion lives in
`server._search`, which loops over the cascade and calls the gateway per query.

## 5. Limits and honesty

- Flood waits: `Busy` before any visible hit → the existing `ToolError`, with the queries
  tried appended ("…; tried: фрукт компан, фрукт"). `Busy` after at least one hit →
  the hits so far are returned with `truncated=True` and the busy sentence appended to
  `note`.
- Hidden chats: a step's hits are counted after the hidden-chat filter, so a step whose only
  hits are hidden counts as zero and `queries_tried` never reveals them.
- Time: the 30 s tool ceiling still applies to the whole cascade.
- Global search (no `chat`) uses the same cascade but with `MAX_QUERIES_GLOBAL = 4`,
  because each global search is heavier for Telegram.
- Spelling variants are tables, not a transliteration library: the tables cover the
  letters that occur in Russian, Polish and English; other scripts pass through untouched.

## 6. Testing

- `tests/test_expand.py`: tokenise (stop words, `@name`, punctuation), stems per language
  with a table of 20 words, variants (both scripts, diacritics, ё), compounds (count cap,
  order), the cascade order and the budget for 1, 2 and 4-word queries, determinism.
- `tests/test_tools_messages.py`: with the fake gateway, "фруктовая компания" over a
  message "Tomek угостил фруктом…"; the seed gets a new chat `-1009` ("Garden Club") in
  `benchdata.build_seed()`: "взяли минитрактор у Tomka", "kosiarka bez problemu",
  "Zielona firma odmówiła", "компания GreenCo ответила через неделю", "Tomek поделился
  теплицей с соседями"; assertions: the hit is found,
  `queries_tried` lists the stems in cascade order with counts, `expand=False` finds nothing,
  `limit` and `truncated` hold, hidden chats stay hidden, `Busy` mid-cascade reports partial
  results with an error? — no: `Busy` on the first query is the existing `ToolError`; `Busy`
  after at least one hit returns the hits found so far with `truncated=True` and the busy
  sentence appended to `note`.
- Bench: 4 new questions in a `search` category over chat -1009 (a Russian question whose
  chat says the word in another form; a Latin-script query over a Cyrillic message; a
  compound; a Polish diacritics-free query), `smoke` unchanged; `test_every_question_is_attainable`
  extended.
- `check.py` unchanged; `snowballstemmer` added to `pyproject.toml` and the lock files.

## 7. Calls made here

- Snowball stems, not a morphological dictionary (small, pure Python, three languages).
- Fixed lists (stop words, compound heads, transliteration) in `expand.py`, not config.
- Expansion on by default; `expand=False` as the opt-out.
- The cascade stops at the first step with hits (precision over recall; the model can ask
  again with a narrower query).
- Budget 6 per chat search, 4 for global.

## 8. Left to the builder

- Exact stop-word and compound-head lists; the transliteration table.
- How `QueryTried` counts hits when a step was cut by `limit`.
- Wording of the one new sentence in the tool description and the note.
