# Smart search v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `search_messages` finds messages whose words appear in another form, script, spelling or as a common compound, by running a short cascade of Telegram searches server-side and reporting what it tried.

**Architecture:** A pure module `tgmcp/expand.py` (tokenise → prefixes → variants → compounds → cascade plan); `server._search` executes the plan against the unchanged `Gateway`, merges and reports `queries_tried`; `SearchResult` gains the field; the fake gateway folds diacritics; the bench gains a `search` category.

**Tech Stack:** `snowballstemmer` (pure Python, new runtime dependency, pinned in both lock files), stdlib.

**Spec:** `docs/history/2026-10-21-smart-search-v1-design.md` (section 3 is the algorithm with the measured stems; section 5 the limits).

## Global Constraints

- `expand.py` is pure (no I/O, no asyncio, deterministic); only `server.py` calls the gateway.
- Budget: `MAX_QUERIES = 6` per chat search, `MAX_QUERIES_GLOBAL = 4`; a repeated query costs nothing; never more Telegram requests than the budget.
- `expand=True` is the default; `expand=False` = one exact search, `queries_tried == []`.
- Hidden chats: hits are counted after the hidden filter; `queries_tried` counts visible hits only.
- `Busy` before any visible hit → `ToolError` with "; tried: q1, q2" appended; after a hit → partial result, `truncated=True`, busy sentence appended to `note`.
- The read-only contract test stays green with no allowlist change; `encoding="utf-8"`, LF, English; the gate exits 0; commits end with the writing model's Co-Authored-By line.

## Review Focus

1. A query of only stop words or punctuation (`"и в"`, `"?"`) must fall back to one exact search with the raw query, never an empty query to Telegram. (Task 1, Task 2)
2. A one-word query must produce exactly one request when it hits, and at most 1 + variants + compounds within the budget when it misses; steps a, b, c that collapse to the same string are sent once. (Task 1)
3. A hit present in two cascade steps (step e compounds) must appear once; `limit` holds after the merge; `truncated` reflects a full page in any step. (Task 2)
4. The global cascade never exceeds 4 requests and never counts a hidden-chat hit as a hit. (Task 2)
5. Cyrillic, Polish and English tokens mixed in one query ("Zielona трактор odmówiła") each get the right stemmer and script variant. (Task 1)

---

### Task 1: `tgmcp/expand.py`

**Files:**
- Create: `tgmcp/expand.py`
- Modify: `pyproject.toml` (dependency `snowballstemmer>=3.1,<4`), `requirements.lock`, `requirements-dev.lock` (re-lock with uv as README/CONTRIBUTING describe)
- Test: `tests/test_expand.py`

**Interfaces:**
- Produces:
  ```python
  MAX_QUERIES = 6; MAX_QUERIES_GLOBAL = 4; MIN_PREFIX = 4; STRIP_FLOOR = 5
  STOP_WORDS: frozenset[str]            # ru/pl/en short function words, lower-case
  COMPOUND_HEADS_CYRILLIC: tuple[str, ...]; COMPOUND_HEADS_LATIN: tuple[str, ...]   # exactly the lists in spec §3.4, in that order
  @dataclass(frozen=True) class Token: text: str; lower: str; script: Literal["cyrillic", "latin", "other"]; position: int
  def tokenize(query: str) -> list[Token]              # drops stop words and tokens < 2 chars; keeps @name and #tag as one token
  def search_prefix(token: Token) -> str               # spec §3.2 (lcp with Snowball stem, floor 4, derivational tail strip at floor 5)
  def variants(prefix: str, script: str) -> list[str]  # spec §3.3, ≤ 3, no duplicates, never the prefix itself
  def compounds(prefix: str, script: str) -> list[str] # heads + prefix, ≤ 20, only for prefixes of 4+ chars
  def rank(tokens: list[Token]) -> list[Token]         # rarest first, spec §3.5 ranking
  @dataclass(frozen=True) class Plan: steps: tuple[tuple[str, str], ...]   # (label, query) with labels "exact", "pair", "single", "variant", "compound"
  def plan(query: str, *, budget: int = MAX_QUERIES) -> Plan                # spec §3.5; a query with no usable tokens → Plan((("exact", query.strip()),))
  ```
- Stemmers are module-level singletons created lazily; `snowballstemmer.stemmer("russian"|"polish"|"english")`.

- [ ] **Step 1: Write the failing tests** (values from the spec's measured table):

```python
@pytest.mark.parametrize("word,prefix", [("фруктовая","фрукт"),("фруктом","фрукт"),("компания","компан"),("компании","компан"),("минитрактор","минитрактор"),("сантехника","сантехник"),("заявка","заявк"),("planting","plant"),("hydraulikiem","hydraulik"),("odmówiła","odmów"),("mowing","mowing"),("Zielona","zielon")])
def test_search_prefix(word, prefix): ...
def test_tokenize_drops_stop_words_and_keeps_handles():   # "про теплицы и @green_co" → ["теплицы", "@green_co"]
def test_variants_other_script_and_diacritics():          # "фрукт" → ["frukt"]; "odmów" → ["odmow"]; "ёлка"-prefix → contains "елк..."; ≤ 3
def test_compounds_capped_and_ordered():                  # "трактор" → starts with "автотрактор", "микротрактор"; len ≤ 20; "liz" (3 chars) → []
def test_rank_prefers_handles_then_capitalised_then_longer():  # "фруктовая компания Zielona" → Zielona first
def test_plan_one_word():       # "фруктовая" → steps: exact "фрукт", then variants, then compounds, len ≤ 6, no duplicate queries
def test_plan_two_words():      # "фруктовая компания" → ("exact","фрукт компан"), ("single","фрукт"), then variants/compounds; "pair" skipped because it equals exact
def test_plan_four_words_budget(): # len(plan("…four words…").steps) ≤ 6; global budget 4
def test_plan_stop_words_only(): # plan("и в") == Plan((("exact","и в"),))
def test_plan_is_deterministic()
```

- [ ] **Step 2: Run to verify they fail** — `No module named tgmcp.expand`.
- [ ] **Step 3: Add the dependency and re-lock** (`uv pip compile … --generate-hashes`, both lock files; `pip install --require-hashes`), **implement `tgmcp/expand.py`.**
- [ ] **Step 4: Run the gate** — exit 0.
- [ ] **Step 5: Commit** — `feat: query expansion module (prefixes, variants, compounds, cascade plan)`

### Task 2: The cascade in `search_messages`

**Files:**
- Modify: `tgmcp/models.py` (`QueryTried`, `SearchResult.queries_tried`, the extended note), `tgmcp/server.py` (`_search`, the tool's `expand` argument and description sentence), `tgmcp/host/benchdata.py` (fake folds diacritics on both sides; new chat `-1009` "Garden Club" with the seed messages of spec §6; nothing else changes), `docs/tools.md` (the new argument and field), `tests/test_tools_messages.py`, `tests/test_benchdata.py`

**Interfaces:**
- `models.QueryTried(query: str, hits: int)`; `SearchResult.queries_tried: list[QueryTried] = []`; `NOTHING_FOR_QUERIES = "Nothing found for any of the {n} queries tried; see queries_tried."` appended to `note` when hits are empty and queries_tried is non-empty.
- `server._search(..., expand: bool)`: builds `expand.plan(query, budget=MAX_QUERIES_GLOBAL if chat is None else MAX_QUERIES)`; for each step: skip if the query string was already sent; call the gateway (`search_in_chat` or `search_global` with the same `topic/sender/since/until/limit`); filter hidden (global); record `QueryTried(query, len(visible))`; steps labelled exact/pair/single/variant stop the cascade on the first visible hit; compound steps continue; merge by `(chat_id, id)`, newest first, cut to `limit`; `truncated` = any step returned `limit` raw hits or the merged union exceeded `limit`; `Busy` handling per Global Constraints. `expand=False` → one gateway call, `queries_tried=[]`, identical to today.
- Tool signature: `search_messages(query, chat=None, topic=None, sender=None, since=None, until=None, limit=30, expand=True)`; description sentence: "The server tries word stems, the other script, Polish without diacritics and common compounds before giving up; pass expand=false for one exact search."

- [ ] **Step 1: Write the failing tests**

```python
async def test_expanded_search_finds_other_forms(call):     # "фруктовая компания" over the message "Tomek угостил фруктом соседей" (a dedicated fake): queries_tried[0] == ("фрукт компан", 0), queries_tried[1] == ("фрукт", 1)
async def test_compound_is_found_after_single_misses(call): # seed "взяли автотрактор у Tomka" only (use a dedicated FakeGateway) → queries_tried contains ("автотрактор", 1); cascade length ≤ 6
async def test_other_script_variant(call):                  # query "трактор" finds "traktor bez problemu" through the variant step: assert ("traktor", 1) in queries_tried when the Cyrillic step misses (dedicated fake with only the Latin message)
async def test_polish_without_diacritics(call):             # "odmowila" finds "Zielona firma odmówiła" (fake folds)
async def test_expand_false_is_one_exact_search(call, seed): # queries_tried == [], seed.calls counts one search
async def test_budget_and_dedup(call, seed):                # a miss everywhere: len(queries_tried) ≤ 6, all queries distinct; global: ≤ 4
async def test_hidden_hits_do_not_count(call):              # a step whose only hits are in -1007 → counted 0, cascade continues, no leak in queries_tried/note
async def test_busy_before_and_after_hits(call):            # raise_next=Busy on first → ToolError text contains "tried:"; Busy on the second step after a hit → partial result, truncated True, note contains the busy sentence
async def test_merge_dedups_and_limits(call):               # compounds overlapping → each hit once; limit honoured; truncated set
def test_seed_garden_club():                                 # -1009 title and the texts exactly as in the spec
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** models, `_search`, the tool argument, the fake's folding (`unicodedata` NFD + strip combining marks on both query and text words), the seed chat, docs/tools.md.
- [ ] **Step 4: Run the gate** — exit 0.
- [ ] **Step 5: Commit** — `feat: search cascade with queries_tried; fake folds diacritics; Garden Club seed`

### Task 3: Bench `search` category and docs

**Files:**
- Modify: `tgmcp/host/benchdata.py` (`QUESTIONS` + 4 `search` questions; `SMOKE_IDS` unchanged), `tgmcp/host/scorer.py` only if a category list is hard-coded, `tests/test_host_scorer.py` (attainability covers the new ones; a negative case where the model passes `expand=False` and misses), `README.md` ("What it can do" one clause), `docs/tools.md` (search semantics section rewritten: what the cascade tries, the budget, `queries_tried`), `docs/ROADMAP.md` (step 1 done; steps 3–4 remain; "model-made synonyms" and "deep scan in memory" added as the next steps)

**Interfaces:**
- Questions (ids `search-1..4`, category `"search"`): Russian question with the word in another form over -1009 (`tool_sequence` requires `search_messages` with `query` containing the stem "теплиц" and `expand` absent or True; `answer_must_contain` a fact from the hit); a Latin-script query over the Cyrillic message; the compound ("минитрактор" found from "трактор"); the Polish diacritics-free query. `must_say_nothing_found` for none of them.

- [ ] **Step 1: Write the failing tests** — the attainability test fails until the questions exist with reachable rules; a negative test: an ideal run that used `expand=False` and found nothing fails `tools_ok`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Add the questions and the docs.**
- [ ] **Step 4: Run the gate** — exit 0; then `tgmcp bench --set smoke --model gemma4:12b` once (fake data) to confirm nothing regressed, and `--set default` filtered to the search category if the CLI allows (otherwise the full default set once).
- [ ] **Step 5: Commit** — `feat: bench search category; docs for the search cascade`
