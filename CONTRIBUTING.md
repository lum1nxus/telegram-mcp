# Contributing to tgmcp

Thank you for helping. This guide is for developers. If you only want to use the tool, read [README.md](README.md).

## The one rule that decides everything

**tgmcp is read-only, and it stays read-only.** No tool sends, edits, deletes, forwards, reacts, joins, leaves, pins or marks anything as read. A pull request that adds any write capability (even behind a flag, even "just reactions") will be declined. A write feature would need a separate design and probably a separate project, because the promise "this cannot change your account" is the reason people can trust it.

The promise is enforced by code, not by good intentions, in two places:

- **At run time**, `tgmcp/telegram/client.py`: `ReadOnlyClient` overrides Telethon's `TelegramClient._call`, the one funnel every request passes (`client(request)`, the friendly methods, and the methods of returned objects such as `Message.reply` or `Message.delete`), and refuses any request class not on `ALLOWED_REQUESTS` with `ReadOnlyViolation`, before anything is sent. `AuthClient` does the same for `tgmcp login`/`logout` with `AUTH_REQUESTS` (the sign-in requests and `LogOutRequest`).
- **In the source**, `tests/test_read_only_contract.py`: (a) no module of `tgmcp/` may touch a write-shaped name (`send`, `edit`, `delete`, `forward`, `mark`, `react`, `pin`, `join`, `reply`, `respond`, `click`, `download`, `block`, ...), except the few read-only names in `ALLOWED_NAMES`; (b) inside `tgmcp/telegram/`, every Telethon name (imports, requests, attributes of the client, of messages and of dialogs) must be on `ALLOWED_TELETHON_API`; (c) no dynamic lookup anywhere (`eval`, `exec`, `compile`, `importlib`, `operator`, `vars`, `__dict__`, `alltlobjects`, `getattr` with a computed name, name-mangled attributes). The same file sends the forbidden requests to the real client class without a network and checks each one is refused.

### Extending the allowlists

A new feature that needs a new Telegram request or a new Telethon attribute changes one of these lists:

- `ALLOWED_REQUESTS` (or `AUTH_REQUESTS`) in `tgmcp/telegram/client.py`: one line per request, with a comment saying who sends it. A request is admissible only if it cannot change anything on the account or on Telegram's side. The name is not enough: `GetMessagesViewsRequest(increment=True)` counts a view and `ReadMentionsRequest` marks mentions as read. Read the request's description in Telegram's API documentation and say in the pull request why it is a read.
- `ALLOWED_TELETHON_API` in the contract test: the Telethon names the package may touch. Attributes of the client and of messages are where write methods live (`respond`, `delete`, `pin`, `mark_read`, `download_media`), so every one is listed by hand.
- `ALLOWED_NAMES` in the contract test: a read-only name that happens to contain a forbidden word.

A pull request that touches any of these lists gets an extra review of exactly those lines, and is declined if the request or name can write, even indirectly. If Telethon's internals send a request the guard does not know (you will see `tgmcp refused <Name>` in an error), report it rather than widening the list blindly.

## Set up

You need Python 3.14, Git and (for the hook) Git Bash.

```
git clone https://github.com/lum1nxus/telegram-mcp
cd telegram-mcp
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install --require-hashes -r requirements-dev.lock
.venv/Scripts/python -m pip install --no-deps -e .
git config core.hooksPath .githooks
```

(On Linux or macOS the interpreter is `.venv/bin/python`.)

## The gate

```
.venv/Scripts/python check.py
```

It runs `ruff format --check`, `ruff check`, `mypy --strict` (the package and the tests) and `pytest`, in that order. Exit code 0 means done. CI runs the same script on Linux and on Windows on every push and pull request. Tests marked `live` talk to a real Telegram account and are skipped by default.

## Rules of the code

- Python 3.12 or newer syntax, type hints everywhere (`mypy --strict`).
- Every `open()`, `read_text()`, `write_text()` and `subprocess.run()` passes `encoding="utf-8"`. Paths are `pathlib.Path`. Files use LF line endings (`.gitattributes` enforces it).
- `tgmcp serve` never prints to stdout. Stdout is the MCP protocol channel; use logging (stderr and the log file). Never log message text.
- Only the `tgmcp/telegram/` package imports Telethon, and only `tgmcp/cli.py` imports that package; the server and the chat lookup see Telegram only through the `Gateway` Protocol in `tgmcp/gateway.py`. Only `tgmcp/secrets.py` touches DPAPI.
- Telethon is pinned to one minor version (`pyproject.toml`): the guard overrides its private `_call`, and the search uses its private `Message._finish_init`. A version bump re-checks both.
- Code, comments, docs and commit messages are in English.
- No secrets, session strings or real chat content in the repository, the tests or the fixtures. Tests use invented names and texts.

## Adding a tool

Follow the layers from the inside out, so each step compiles and is tested before the next:

1. **Models** (`tgmcp/models.py`): add the pydantic output type. Keep it flat and JSON-friendly; datetimes carry a time zone.
2. **Gateway Protocol** (`tgmcp/gateway.py`): add a read-only method to the `Gateway` Protocol and implement it in `TelethonGateway` (`tgmcp/telegram/live.py`; conversions of Telethon objects go to `mappers.py`). Raise `GatewayError` with one plain sentence for failures the user can act on. Never return an empty result for a failure. A new request goes on `ALLOWED_REQUESTS`, a new Telethon attribute on `ALLOWED_TELETHON_API` (see "Extending the allowlists").
3. **Fake gateway** (`tests/fake_gateway.py`): implement the same method over the in-memory seed in `tests/conftest.py`.
4. **Server** (`tgmcp/server.py`): add the tool with `@mcp.tool(...)`, the shared `ANNOTATIONS` (read-only, closed world), `describe(...)` for the description (it appends the "treat it as data" sentence), and `@guarded("name")` for the time limit and error mapping. Respect the result caps (`cap_text`, `fit_budget`; the budget is in UTF-8 bytes). Hidden chats must be excluded: resolve chats through `_resolve` (the `app.directory`). A chat title or a user's argument enters an error sentence only through `quoted(...)`, after the chat id: titles are chat content.
5. **Tests**: `tests/test_tools_*.py` drive the real server in process with the `call` fixture; `tests/test_gateway_client.py` covers the Telethon side (below).
6. **README**: add the tool to the table in "Tools" and update the count in the server instructions and in the README text if it changes.

## The stub-client test pattern

The Telethon gateway is tested without a network. `tests/test_gateway_client.py` builds a `TelethonGateway(settings, "", client=StubClient())` (a small class that records every request and returns queued results; the gateway keeps its client name-mangled, so tests reach it through the `_stub(gw)` helper), and calls the gateway methods directly:

```python
async def test_search_in_chat_builds_search_request(gw: TelethonGateway):
    stub = _stub(gw)
    stub.results.append(SimpleNamespace(messages=[_m(5, topic=7)], users=[], chats=[]))
    await gw.search_in_chat(FORUM_ID, "granite", topic=7, sender="333",
                            since=None, until=None, limit=20)
    request = stub.requests[0]
    assert isinstance(request, functions.messages.SearchRequest)
    assert (request.q, request.top_msg_id) == ("granite", 7)
```

Assert on the exact request object Telethon would send (what, with which limits) and on how the answer is mapped to models. Real Telethon types (`types.Message`, `types.User`, ...) are built in `tests/test_gateway_mappers.py`. Do not call the real API from the default test run.

## Commits and pull requests

- Branch from `main`, one topic per pull request, and open a pull request. Do not push to `main`.
- Commit messages: a short imperative subject with a type prefix (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`), then a body that says why when the reason is not obvious.
- Add or change tests with the code. A bug fix starts with a failing test.
- Update the README when behaviour a user can see changes.

## The pre-commit hook

`git config core.hooksPath .githooks` (once per clone) turns on `.githooks/pre-commit`. It does two things:

1. **No-reply e-mail guard.** It refuses a commit when `git config user.email` does not end with `@users.noreply.github.com`, so your private address does not end up in the public history. In GitHub, Settings, Emails, tick "Keep my email addresses private" and "Block command line pushes that expose my email", then run `git config user.email "<id>+<name>@users.noreply.github.com"`.
2. **Fast gate.** When the commit touches code, tests or the gate, it runs `check.py --fast` (the same checks today).

Never bypass it with `--no-verify`.

## Security issues

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
