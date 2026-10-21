# Local models (`tgmcp ask`, `tgmcp bench`, LM Studio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a local model (Ollama or LM Studio) answer questions about the user's Telegram chats through the unchanged `tgmcp` tools, with a measured choice of model and a code-enforced rule that nothing leaves the computer.

**Architecture:** A new package `tgmcp/host/` holds a tiny MCP host: `backends.py` (HTTP to `127.0.0.1` only, two dialects), `loop.py` (question → model → tool calls through the in-process MCP `Client` → answer), `benchdata.py` (the fake gateway, seed and question set, moved out of `tests/`), `scorer.py` (rule-based scoring), `prompt.py`. The CLI gains `ask` and `bench`. Nothing in `tgmcp/` outside `tgmcp/telegram/` imports telethon (the contract test keeps enforcing it).

**Tech Stack:** Python 3.14, stdlib `urllib`/`json`, mcp 2.3 in-process `Client`, pydantic (already present). No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-21-local-models-design.md` (sections 5 and 8 hold the exact values).

## Global Constraints

- Windows first; `encoding="utf-8"`; LF; English; `check.py` exits 0 (ruff, mypy --strict on `tgmcp/` and `check.py`, pytest without `live` and `local`).
- `tgmcp/host/` imports no telethon and nothing from `tests/`; the read-only contract test (allowlist version from the Fable fix wave) must keep passing — `host` modules must not use `eval`, `exec`, `getattr` with a computed name, `importlib`, `vars(`, `__dict__`.
- Local-only: hosts `127.0.0.1`, `localhost`, `::1`, `[::1]`; model names ending in `-cloud` refused; Ollama `/api/show` must describe local weights. Error sentences verbatim: `"tgmcp talks to local models only; <host> is not this computer."`, `"Model <name> is served from the cloud, not from this computer."`.
- Defaults: backend `ollama`, URLs `http://127.0.0.1:11434` and `http://127.0.0.1:1234`, temperature 0, `num_ctx` 16384, `think` off, `max_steps` 8, `HOST_RESULT_CAP = 16_000` characters with the cut line `[cut by the host: ask with a smaller limit or page with before_id]`.
- `ask` prints only the final answer on stdout (or `(unfinished after N steps)` + last text / tool-call list, exit 1); everything else on stderr. `bench` never builds a Telethon gateway.
- Commits end with the Co-Authored-By line of the model that wrote them.

## Review Focus

1. A model that returns tool arguments of the wrong type (`limit: "50"`) must get the MCP validation text back and be allowed to retry within `max_steps`, never crash the loop. (Task 3)
2. A model that returns a tool name that does not exist must get one corrective message, and a second malformed call ends the run with the unfinished output. (Task 3)
3. `--url http://127.0.0.1@evil.example/…`, `http://localhost.evil.example`, `http://[::1]:11434`, `HTTP://LOCALHOST:11434` must be classified correctly by `local_only`. (Task 2)
4. A tool result over 16 000 characters is cut once, keeps valid UTF-8, and the cut is counted by `bench`. (Task 3, Task 4)
5. Moving the fake gateway into the package must not change what the v1 tests assert (same ids, titles, dates); adding chat -1008 must not disturb counts the v1 tests rely on (`chats: N`, pagination ids in -1001). (Task 1)

---

### Task 1: Bench data in the package

**Files:**
- Create: `tgmcp/host/__init__.py`, `tgmcp/host/benchdata.py`
- Modify: `tests/fake_gateway.py` → becomes a two-line re-export; `tests/factories.py` → re-export; `tests/conftest.py` imports the seed builder
- Test: `tests/test_benchdata.py`

**Interfaces:**
- Produces: `benchdata.FakeGateway` (moved verbatim), `benchdata.make_chat/make_message/make_topic` (moved), `benchdata.build_seed() -> FakeGateway` (the v1 seed plus chat `-1008` titled `"Sąsiedzi / Соседи"`, kind group, 10 messages ids 1–10: five Polish (`"Hydraulik przyjdzie we wtorek o 9"`, `"Ktoś ma klucz do piwnicy?"`, `"Awaria windy zgłoszona"`, `"Remont klatki od poniedziałku"`, `"Zbiórka na domofon: 50 zł"`) and five Russian (`"Сантехник придёт во вторник в 9"`, `"У кого ключ от подвала?"`, `"Лифт сломался, заявка подана"`, `"Ремонт подъезда с понедельника"`, `"Сбор на домофон: 50 злотых"`), dated hourly back from `_NOW`, sender 444 `"Kasia"`), `benchdata.NOW: datetime`.
- The fake's search semantics stay word-prefix on `re.findall(r"\w+")` (works for Cyrillic and Polish letters).

- [ ] **Step 1: Write the failing test**

```python
def test_seed_has_multilingual_chat():
    seed = benchdata.build_seed()
    chats = {c.id: c for c in asyncio.run(seed.dialogs())}
    assert chats[-1008].title == "Sąsiedzi / Соседи"
    hits = asyncio.run(seed.search_in_chat(-1008, "лифт", topic=None, sender=None, since=None, until=None, limit=10))
    assert [m.id for m in hits] == [8]
def test_v1_seed_unchanged():   # ids 1..120 in -1001, id 120 text length 4096, chats -1001..-1007 titles as before
```

- [ ] **Step 2: Run to verify it fails** — `No module named tgmcp.host`.
- [ ] **Step 3: Move the code; make `tests/fake_gateway.py` and `tests/factories.py` re-export (`from tgmcp.host.benchdata import FakeGateway  # noqa: F401`), `conftest.seed` return `build_seed()`.**
- [ ] **Step 4: Run the gate** — exit 0, all v1 tests still pass.
- [ ] **Step 5: Commit** — `refactor: bench data and the fake gateway live in tgmcp.host`

### Task 2: Backends and the local-only rule

**Files:**
- Create: `tgmcp/host/backends.py`
- Test: `tests/test_host_backends.py`

**Interfaces:**
- Produces:
  ```python
  class RemoteRefused(Exception): ...
  class BackendError(Exception): ...        # one sentence: unreachable, unknown model, bad JSON
  def local_only(url: str) -> str           # returns the normalised base URL or raises RemoteRefused
  @dataclass(frozen=True) class ToolCall: id: str; name: str; arguments: dict[str, Any]
  @dataclass(frozen=True) class Reply: text: str; tool_calls: list[ToolCall]; raw: dict[str, Any]
  class ChatBackend(Protocol):
      name: str; model: str
      async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply: ...
      async def check_model(self) -> None   # raises BackendError / RemoteRefused
  class OllamaBackend: __init__(self, model: str, url: str = "http://127.0.0.1:11434", *, think: bool = False, num_ctx: int = 16384, opener: Callable[..., Any] = urllib.request.urlopen)
  class OpenAICompatBackend: __init__(self, model: str, url: str = "http://127.0.0.1:1234", *, opener=...)
  def make_backend(kind: Literal["ollama", "lmstudio"], model: str | None, url: str | None, *, think: bool) -> ChatBackend
  ```
  Ollama: `POST /api/chat` with `{"model", "messages", "tools", "stream": false, "think": think, "options": {"temperature": 0, "num_ctx": num_ctx}}`; tool calls at `message.tool_calls[].function.{name, arguments}` (arguments already an object); tool results sent as `{"role": "tool", "content": text, "tool_name": name}`; `check_model`: `GET /api/tags` must list the model (else `BackendError("Model X is not installed; run `ollama pull X`.")`), name ending `-cloud` → `RemoteRefused`, `POST /api/show {"model"}` must contain `details.parameter_size` and `details.quantization_level` (else `RemoteRefused` cloud sentence). Model default when `None`: first entry of `/api/tags`.
  OpenAI dialect (LM Studio): `POST /v1/chat/completions` with `{"model", "messages", "tools", "temperature": 0}`; tool calls at `choices[0].message.tool_calls[].function.{name, arguments}` where `arguments` is a JSON **string** (parse; a parse failure yields a `ToolCall` with `arguments={"_malformed": raw}` so the loop can correct the model); tool results as `{"role": "tool", "tool_call_id": id, "content": text}`; `check_model`: `GET /v1/models` lists it. Default model: first of `/v1/models`.
  Both: network errors (`URLError`, `TimeoutError`, HTTP ≥ 400) → `BackendError` with the spec's sentences; request timeout 120 s; reasoning fields (`message.thinking`, `reasoning_content`) are dropped from `Reply.text`.

- [ ] **Step 1: Write the failing tests** — `local_only` table (Review Focus 3 plus `http://127.0.0.1:11434/`, `http://localhost`, `http://192.168.1.5:11434` refused, `http://127.0.0.1.evil.example` refused); `OllamaBackend.chat` with a fake opener returning a canned tool-call JSON → `Reply.tool_calls == [ToolCall(...)]`, request body asserted (`stream` false, `think` false, `num_ctx` 16384, tools passed through); `check_model` cases (not installed, `-cloud`, no `details` → cloud sentence, ok); `OpenAICompatBackend` parses a JSON-string `arguments` and flags a malformed one; unreachable → `BackendError` with "is not running at".
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `tgmcp/host/backends.py`** (urllib only; `opener` injected for tests).
- [ ] **Step 4: Run the gate** — exit 0.
- [ ] **Step 5: Commit** — `feat: local model backends with the local-only rule`

### Task 3: The agent loop

**Files:**
- Create: `tgmcp/host/loop.py`, `tgmcp/host/prompt.py`
- Test: `tests/test_host_loop.py`

**Interfaces:**
- Consumes: `ChatBackend`, `Reply`, `ToolCall`; `mcp.Client`; `tgmcp.server.build_server`.
- Produces:
  ```python
  HOST_RESULT_CAP = 16_000
  CUT_LINE = "[cut by the host: ask with a smaller limit or page with before_id]"
  SYSTEM_PROMPT: str   # prompt.py, one paragraph per spec section 4 rule 4
  @dataclass class Step: call: ToolCall; result_text: str; is_error: bool; cut: bool; seconds: float
  @dataclass class RunResult: answer: str | None; steps: list[Step]; finished: bool; messages: list[dict[str, Any]]
  async def run_question(question: str, backend: ChatBackend, server: MCPServer, *, max_steps: int = 8) -> RunResult
  def mcp_tools_for_model(tools: list[mcp.types.Tool]) -> list[dict[str, Any]]   # {"type":"function","function":{"name","description","parameters": input_schema}}
  def result_text(result: CallToolResult) -> str   # structured_content as compact JSON when present, else the text blocks joined; then capped with CUT_LINE
  ```
  Loop: open `Client(server)`, `list_tools()`, messages = [system, user]; up to `max_steps` model calls; each `ToolCall` → `call_tool(name, arguments)`; an unknown tool or `_malformed` arguments → the tool message `"Unknown tool or malformed arguments; call one of: <names>."` (one correction allowed; the second ends the run with `finished=False`); `is_error` results are forwarded verbatim (the MCP text); a reply with no tool calls ends the run with `answer=reply.text`.

- [ ] **Step 1: Write the failing tests** with a `FakeBackend(replies: list[Reply])` that pops scripted replies and records the messages it received: happy path (tool call → final text; `steps` has one entry with `is_error False`); wrong argument type → the MCP validation text is in the next messages and the model's second call succeeds; unknown tool twice → `finished False`; `max_steps` reached → `finished False`, `answer` is the last text; a 40 000-character fake result → `cut True`, result ends with `CUT_LINE`, valid UTF-8 (cut on a multi-byte boundary test with Cyrillic); `mcp_tools_for_model` output has six entries with the server's descriptions.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `loop.py` and `prompt.py`.**
- [ ] **Step 4: Run the gate** — exit 0.
- [ ] **Step 5: Commit** — `feat: local MCP host loop`

### Task 4: Bench questions and the scorer

**Files:**
- Modify: `tgmcp/host/benchdata.py` (add `QUESTIONS`)
- Create: `tgmcp/host/scorer.py`
- Test: `tests/test_host_scorer.py`

**Interfaces:**
- Produces in `benchdata.py`: `@dataclass(frozen=True) class Question: id: str; category: Literal["lookup","history","paging","context","topic","hidden","refusal","multilingual"]; text: str; tool_sequence: list[tuple[str, dict[str, Callable[[Any], bool]]]]; answer_must_contain: list[str]; answer_must_not_contain: list[str]; must_report_nothing_found: bool = False`; `QUESTIONS: list[Question]` with 30 entries (at least 3 per category; `smoke` = ids `lookup-1, history-1, paging-1, hidden-1, multilingual-1`), `SMOKE_IDS`.
- Produces in `scorer.py`: `@dataclass class Score: tools_ok: bool; args_ok: bool; answer_ok: bool; no_invention: bool; steps: int; cuts: int; seconds: float`; `def score(q: Question, run: RunResult, seed_titles: set[str], seed_ids: set[int]) -> Score`; `def table(rows: list[tuple[Question, Score]], header: str) -> str` (per category: n, tools %, args %, answer %, invention %, mean steps, mean s; a total line; footer: "no_invention checks ids and exact chat titles only"); `NOTHING_FOUND_MARKERS = ("nothing", "no messages", "not found", "ничего", "не найдено", "nic", "brak")`.
  `no_invention`: every `-100…`/numeric id and every seed chat title mentioned in the answer must appear in the concatenated tool results of the run.

- [ ] **Step 1: Write the failing tests** — `score` on hand-written `RunResult`s: tools in order pass / wrong order fail; argument predicate pass/fail; `answer_must_contain` and `must_not_contain`; `must_report_nothing_found` with a Russian marker; `no_invention` true when the answer cites an id present in results and false when it cites `-1009`; `table` renders 8 categories and the footer; `QUESTIONS` has 30 unique ids, ≥ 3 per category, `SMOKE_IDS ⊂ ids`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Write the 30 questions** (English, Russian and Polish; over the seed: granite route in Hiking Club, the hidden "Private" chat must not be found, the forum "Cars" topic, paging through -1001 with `before_id == 71`, context around id 50, the Sąsiedzi chat in Polish and Russian) **and `scorer.py`.**
- [ ] **Step 4: Run the gate** — exit 0.
- [ ] **Step 5: Commit** — `feat: bench question set and rule-based scorer`

### Task 5: CLI, config, README

**Files:**
- Modify: `tgmcp/cli.py`, `tgmcp/config.py` (`local_model` kept by `save_credentials`/`forget_credentials`; `Settings.local_model: str | None`), `README.md`, `docs/ROADMAP.md`, `pyproject.toml` (marker `local`, `addopts = "-m 'not live and not local'"`)
- Create: `tests/test_cli_host.py`, `tests/test_local.py`
- Test: as named

**Interfaces:**
- `tgmcp ask QUESTION [--backend ollama|lmstudio] [--model NAME] [--url URL] [--max-steps 8] [--show-calls] [--think]` → `make_backend` → `check_model` → `run_question(question, backend, build_server(factory, settings))` → stdout per spec 5.1/8; exit 0/1; `RemoteRefused`/`BackendError`/`NotLoggedIn` → sentence on stderr, exit 1 (NotLoggedIn: exit 2 as elsewhere).
- `tgmcp bench --model NAME [--backend] [--url] [--set default|smoke] [--repeat 1] [--think] [--json PATH]` → `build_server(lambda s: build_seed(), settings)` (never Telethon) → runs the set, prints `table(...)` with header `model=<name> backend=<kind> think=<on|off> repeat=<n>`; `--json` writes `[{"id", "category", "score": {...}, "messages": [...]}]`.
- README chapter "Local models (nothing leaves your computer)": why; LM Studio (download, model, `mcp.json` fragment with `C:\path\to\telegram-mcp`, context ≥ 8 K, "Serve on local network" off, the confirmation dialog); Ollama + `tgmcp ask` (install Ollama, `ollama pull qwen3.5:9b`, `.venv\Scripts\tgmcp ask "…"`, cmd and Git Bash variants); what the local-only rule checks and what it cannot see (LM Studio's own cloud features); `tgmcp bench` and how to read the table; the candidate models with the published numbers as pointers (`qwen3.5:9b` BFCL-V4 66.1 per vdf.ai; `gemma4:12b`; `ministral-3:14b`; `qwen3:14b`) and the sentence that our `bench` decides; limits (one question per run, answers weaker than Claude on long summaries, `no_invention` scope). ROADMAP: remove what landed, add `--follow-up`, model-judge scoring, LM Studio ephemeral MCP via its API.

- [ ] **Step 1: Write the failing tests** — `test_cli_host.py`: `ask` with a stubbed `make_backend`/`run_question` prints only the answer; unfinished run prints `(unfinished after 8 steps)` and exits 1; `--url http://10.0.0.5:11434` → stderr sentence, exit 1; `bench --set smoke` with a `FakeBackend` scripted to pass all five prints a table with `100` in the total line and never calls `tgmcp.telegram.factory` (monkeypatch it to raise); `--json` file shape. `test_local.py` (marker `local`, skipped unless `GET http://127.0.0.1:11434/api/tags` answers): `bench --set smoke --model $TGMCP_BENCH_MODEL` returns 0 and prints a table.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement the commands, config field, README and ROADMAP.**
- [ ] **Step 4: Run the gate** — exit 0 (`local` deselected).
- [ ] **Step 5: Commit** — `feat: tgmcp ask and tgmcp bench; README chapter on local models`

### Task 6: The author's measurements (manual)

- [ ] `ollama pull qwen3.5:9b`, `ollama pull ministral-3:14b`, `ollama pull qwen3:14b` (gemma4:12b is installed).
- [ ] For each: `.venv\Scripts\tgmcp bench --model NAME --repeat 2 --json docs\benchmarks\2026-MM-DD-NAME.json` and once with `--think`; paste the table into `docs/benchmarks/2026-MM-DD-NAME.md`.
- [ ] README: name the best two models with their scores; open a PR.
- [ ] Try LM Studio with the same server and note any dialog or `mcp.json` difference in the README.
