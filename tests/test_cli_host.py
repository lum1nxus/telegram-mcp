"""`tgmcp ask` and `tgmcp bench`: the backend and the loop are stubbed, never a real model."""

import json
from pathlib import Path
from typing import Any

import pytest

import tgmcp.telegram
from tests.test_host_scorer import IDEAL
from tgmcp import cli, config
from tgmcp.config import Settings
from tgmcp.gateway import NotLoggedIn
from tgmcp.host.backends import BackendError, ModelTurn, OllamaBackend, RemoteRefused, ToolCall
from tgmcp.host.benchdata import QUESTIONS, SMOKE_IDS, build_seed
from tgmcp.host.loop import RunResult, Step

BY_TEXT = {q.text: q.id for q in QUESTIONS}


class StubBackend:
    name = "ollama"

    def __init__(self, model: str | None, *, fail: Exception | None = None) -> None:
        self.model = model or "first-model"
        self.fail = fail

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        raise AssertionError("the loop is stubbed")

    async def check_model(self) -> None:
        if self.fail is not None:
            raise self.fail


class ScriptedBackend:
    """Answers each bench question with its ideal tool calls, then its correct answer."""

    name = "ollama"

    def __init__(self, model: str = "scripted", *, wrong: str | None = None) -> None:
        self.model = model
        self.wrong = wrong

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        question_id = BY_TEXT[messages[1]["content"]]
        calls, answer = IDEAL[question_id]
        done = sum(1 for m in messages if m["role"] == "assistant")
        if question_id == self.wrong:
            return ModelTurn("I do not know.", (), {})
        if done < len(calls):
            name, arguments = calls[done]
            return ModelTurn("", (ToolCall(f"c{done}", name, arguments),), {})
        return ModelTurn(answer, (), {})

    async def check_model(self) -> None:
        return None


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TGMCP_HOME", str(tmp_path))
    monkeypatch.setenv("TGMCP_API_ID", "1")
    monkeypatch.setenv("TGMCP_API_HASH", "a")
    monkeypatch.delenv("TGMCP_HIDDEN_CHATS", raising=False)
    monkeypatch.delenv("TGMCP_EXPECTED_USER", raising=False)
    monkeypatch.delenv("TGMCP_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(config, "restrict_to_owner", lambda path: None)
    monkeypatch.setattr(cli, "factory", lambda settings: build_seed())
    return tmp_path


@pytest.fixture
def no_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bench` must never construct a Telethon gateway, nor need a login."""

    def refuse(settings: Settings) -> Any:
        raise AssertionError("bench touched the Telegram factory")

    monkeypatch.setattr(cli, "factory", refuse)
    monkeypatch.setattr(tgmcp.telegram, "factory", refuse)
    monkeypatch.delenv("TGMCP_API_ID", raising=False)
    monkeypatch.delenv("TGMCP_API_HASH", raising=False)


def _backends(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[dict[str, Any]]:
    """Stub `make_backend`; return the list of the calls it received."""
    seen: list[dict[str, Any]] = []

    def make(kind: str, model: str | None, url: str | None, *, think: bool) -> StubBackend:
        seen.append({"kind": kind, "model": model, "url": url, "think": think})
        return StubBackend(model, **kwargs)

    monkeypatch.setattr(cli, "make_backend", make)
    return seen


def _loop(monkeypatch: pytest.MonkeyPatch, result: RunResult) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def run(question: str, backend: Any, server: Any, *, max_steps: int) -> RunResult:
        seen.append({"question": question, "max_steps": max_steps, "model": backend.model})
        return result

    monkeypatch.setattr(cli, "run_question", run)
    return seen


def _step(name: str, text: str = "x" * 12, *, is_error: bool = False, cut: bool = False) -> Step:
    call = ToolCall("c", name, {"chat": "-1001", "limit": 5})
    return Step(call, text, is_error=is_error, cut=cut, seconds=0.1)


# --- ask ----------------------------------------------------------------------------------------


def test_ask_prints_only_the_answer(env, capsys, monkeypatch):
    backends = _backends(monkeypatch)
    runs = _loop(monkeypatch, RunResult("Hiking Club (-1001)", [], True, [], 1.0))
    assert cli.main(["ask", "Find my hiking group."]) == 0
    out, err = capsys.readouterr()
    assert out == "Hiking Club (-1001)\n"
    assert err == ""
    assert backends == [{"kind": "ollama", "model": None, "url": None, "think": False}]
    assert runs == [{"question": "Find my hiking group.", "max_steps": 8, "model": "first-model"}]


def test_ask_passes_its_options(env, capsys, monkeypatch):
    backends = _backends(monkeypatch)
    runs = _loop(monkeypatch, RunResult("ok", [], True, [], 1.0))
    argv = ["ask", "q", "--backend", "lmstudio", "--model", "m", "--url", "http://localhost:1"]
    assert cli.main([*argv, "--max-steps", "3", "--think"]) == 0
    assert backends == [
        {"kind": "lmstudio", "model": "m", "url": "http://localhost:1", "think": True}
    ]
    assert runs[0]["max_steps"] == 3


def test_ask_model_from_config_then_env(env, capsys, monkeypatch):
    monkeypatch.delenv("TGMCP_API_ID")
    (env / "config.json").write_text('{"api_id": 1, "local_model": "qwen3:14b"}', encoding="utf-8")
    backends = _backends(monkeypatch)
    _loop(monkeypatch, RunResult("ok", [], True, [], 1.0))
    assert cli.main(["ask", "q"]) == 0
    monkeypatch.setenv("TGMCP_LOCAL_MODEL", "gemma4:12b")
    assert cli.main(["ask", "q"]) == 0
    assert cli.main(["ask", "q", "--model", "given"]) == 0
    assert [b["model"] for b in backends] == ["qwen3:14b", "gemma4:12b", "given"]


def test_ask_unfinished_prints_the_last_text_and_exits_1(env, capsys, monkeypatch):
    _backends(monkeypatch)
    steps = [_step("get_messages") for _ in range(11)]  # several calls in some turns
    _loop(monkeypatch, RunResult("Partial: 120 and 119.", steps, False, [], 1.0, turns=8))
    assert cli.main(["ask", "q"]) == 1
    out, err = capsys.readouterr()
    assert out == "(unfinished after 8 steps)\nPartial: 120 and 119.\n"
    assert err == ""


def test_ask_unfinished_without_text_lists_the_calls(env, capsys, monkeypatch):
    _backends(monkeypatch)
    steps = [_step("list_chats"), _step("get_messages")]
    _loop(monkeypatch, RunResult(None, steps, False, [], 1, turns=2))
    assert cli.main(["ask", "q"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "(unfinished after 2 steps)"
    assert lines[1].startswith("-> list_chats(")
    assert lines[2].startswith("-> get_messages(")


def test_ask_show_calls_goes_to_stderr(env, capsys, monkeypatch):
    _backends(monkeypatch)
    steps = [_step("get_messages", "y" * 40, cut=True), _step("get_chat", is_error=True)]
    _loop(monkeypatch, RunResult("done", steps, True, [], 1.0))
    assert cli.main(["ask", "q", "--show-calls"]) == 0
    out, err = capsys.readouterr()
    assert out == "done\n"
    assert err.splitlines() == [
        '-> get_messages(chat="-1001", limit=5) 40 chars, cut',
        '-> get_chat(chat="-1001", limit=5) 12 chars, error',
    ]


def test_ask_refuses_a_remote_url_before_touching_telegram(env, capsys, monkeypatch):
    def refuse(settings: Settings) -> Any:
        raise AssertionError("Telegram must not be touched")

    monkeypatch.setattr(cli, "factory", refuse)
    assert cli.main(["ask", "q", "--url", "http://10.0.0.5:11434"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == "tgmcp talks to local models only; 10.0.0.5 is not this computer."


def test_ask_backend_error_is_one_sentence(env, capsys, monkeypatch):
    sentence = "Ollama is not running at http://127.0.0.1:11434; start it or pass --url."
    _backends(monkeypatch, fail=BackendError(sentence))
    assert cli.main(["ask", "q"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == sentence


def test_ask_not_logged_in_exits_2(env, capsys, monkeypatch):
    def not_logged_in(settings: Settings) -> Any:
        raise NotLoggedIn()

    monkeypatch.setattr(cli, "factory", not_logged_in)
    _backends(monkeypatch)
    assert cli.main(["ask", "q"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "tgmcp login" in err


def test_help_lists_ask_and_bench(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "ask" in out
    assert "bench" in out


# --- bench --------------------------------------------------------------------------------------


def _scripted(monkeypatch: pytest.MonkeyPatch, backend: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def make(kind: str, model: str | None, url: str | None, *, think: bool) -> Any:
        seen.append({"kind": kind, "model": model, "url": url, "think": think})
        return backend

    monkeypatch.setattr(cli, "make_backend", make)
    return seen


def _total(out: str) -> list[str]:
    line = next(line for line in out.splitlines() if line.startswith("total"))
    return line.split()


def test_bench_smoke_scores_100_without_telegram(no_telegram, capsys, monkeypatch):
    seen = _scripted(monkeypatch, ScriptedBackend("gemma4:12b"))
    assert cli.main(["bench", "--model", "gemma4:12b", "--set", "smoke"]) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[0] == "model=gemma4:12b backend=ollama ctx=16384 think=off repeat=1"
    assert _total(out)[1:6] == ["5", "100%", "100%", "100%", "100%"]
    assert seen == [{"kind": "ollama", "model": "gemma4:12b", "url": None, "think": False}]
    assert err == ""


def test_bench_header_for_lmstudio(no_telegram, capsys, monkeypatch):
    _scripted(monkeypatch, ScriptedBackend("qwen"))
    assert cli.main(["bench", "--backend", "lmstudio", "--model", "qwen", "--set", "smoke"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "model=qwen backend=lmstudio ctx=app think=n/a repeat=1"


def test_bench_header_shows_the_ollama_context(no_telegram, capsys, monkeypatch):
    real = OllamaBackend("m", num_ctx=8192)
    _scripted(monkeypatch, real)

    async def no_runs(*args: Any) -> tuple[list[Any], None]:
        return [], None

    monkeypatch.setattr(cli, "_bench", no_runs)
    assert cli.main(["bench", "--model", "m", "--set", "smoke"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "model=m backend=ollama ctx=8192 think=off repeat=1"


def test_bench_one_wrong_answer_scores_less(no_telegram, capsys, monkeypatch):
    _scripted(monkeypatch, ScriptedBackend(wrong="lookup-1"))
    assert cli.main(["bench", "--model", "m", "--set", "smoke"]) == 0
    assert _total(capsys.readouterr().out)[4] == "80%"


def test_bench_json_and_repeat(no_telegram, tmp_path, capsys, monkeypatch):
    _scripted(monkeypatch, ScriptedBackend())
    path = tmp_path / "log.json"
    argv = ["bench", "--model", "m", "--set", "smoke", "--repeat", "2", "--think"]
    assert cli.main([*argv, "--json", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "model=scripted backend=ollama ctx=16384 think=on repeat=2"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert [(r["id"], r["run"]) for r in rows] == [(i, n) for i in SMOKE_IDS for n in (1, 2)]
    first = rows[0]
    assert first["category"] == "lookup"
    assert set(first["score"]) >= {"tools_ok", "args_ok", "answer_ok", "no_invention", "steps"}
    assert first["score"]["answer_ok"] is True
    assert first["messages"][0]["role"] == "system"
    assert "error" not in first


def test_bench_default_set_runs_every_question(no_telegram, capsys, monkeypatch):
    _scripted(monkeypatch, ScriptedBackend())
    assert cli.main(["bench", "--model", "m"]) == 0
    assert _total(capsys.readouterr().out)[1:6] == [str(len(QUESTIONS)), *["100%"] * 4]


def test_bench_a_crashing_question_scores_zero_and_the_bench_goes_on(
    no_telegram, tmp_path, capsys, monkeypatch
):
    class Crashing(ScriptedBackend):
        async def chat(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelTurn:
            if BY_TEXT[messages[1]["content"]] == "paging-1":
                raise ValueError("boom")
            return await super().chat(messages, tools)

    _scripted(monkeypatch, Crashing())
    path = tmp_path / "log.json"
    assert cli.main(["bench", "--model", "m", "--set", "smoke", "--json", str(path)]) == 0
    assert _total(capsys.readouterr().out)[1:6] == ["5", "80%", "80%", "80%", "80%"]
    crashed = next(r for r in json.loads(path.read_text(encoding="utf-8")) if r["id"] == "paging-1")
    assert crashed["error"] == "ValueError"
    assert crashed["score"]["steps"] == 0


def test_bench_backend_error_at_start_exits_1(no_telegram, capsys, monkeypatch):
    sentence = "Model x is not installed; run `ollama pull x`."
    _scripted(monkeypatch, StubBackend("x", fail=BackendError(sentence)))
    assert cli.main(["bench", "--model", "x"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == sentence


def test_bench_backend_error_during_a_run_is_reported_and_exits_1(no_telegram, capsys, monkeypatch):
    class Dying(ScriptedBackend):
        async def chat(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelTurn:
            raise BackendError("Ollama returned an answer tgmcp cannot read.")

    _scripted(monkeypatch, Dying())
    assert cli.main(["bench", "--model", "m", "--set", "smoke"]) == 1
    out, err = capsys.readouterr()
    assert _total(out)[1:3] == ["5", "0%"]
    assert err.strip() == "Ollama returned an answer tgmcp cannot read."


def test_bench_refuses_a_remote_url(no_telegram, capsys):
    assert cli.main(["bench", "--model", "m", "--url", "http://example.com:11434"]) == 1
    assert "not this computer" in capsys.readouterr().err


def test_ask_backend_error_inside_the_loop_is_one_sentence(env, capsys, monkeypatch):
    class Dying(ScriptedBackend):
        async def chat(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelTurn:
            raise BackendError("Ollama returned an answer tgmcp cannot read.")

    _scripted(monkeypatch, Dying())
    assert cli.main(["ask", "q"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == "Ollama returned an answer tgmcp cannot read."


@pytest.mark.parametrize(
    "argv", [["bench", "--model", "m", "--repeat", "0"], ["ask", "q", "--max-steps", "0"]]
)
def test_counts_must_be_positive(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    assert stop.value.code == 2
    assert "must be 1 or more" in capsys.readouterr().err


def _refuse_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(settings: Settings) -> Any:
        raise AssertionError("Telegram must not be touched")

    monkeypatch.setattr(cli, "factory", refuse)


@pytest.mark.parametrize(
    "error",
    [
        RemoteRefused("Model x-cloud is served from the cloud, not from this computer."),
        BackendError("Model x is not installed; run `ollama pull x`."),
    ],
)
def test_ask_checks_the_model_before_touching_telegram(env, capsys, monkeypatch, error):
    _refuse_factory(monkeypatch)
    _backends(monkeypatch, fail=error)
    assert cli.main(["ask", "q", "--model", "x"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == str(error)


@pytest.mark.parametrize("command", [["ask", "q"], ["bench", "--model", "m"]])
def test_think_with_lmstudio_is_refused(env, capsys, monkeypatch, command):
    _refuse_factory(monkeypatch)
    assert cli.main([*command, "--backend", "lmstudio", "--think"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == "--think is only available with --backend ollama."


def test_bench_crash_is_reported_and_keeps_the_messages_so_far(
    no_telegram, tmp_path, capsys, monkeypatch
):
    class CrashOnSecondTurn(ScriptedBackend):
        async def chat(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelTurn:
            if BY_TEXT[messages[1]["content"]] == "paging-1" and len(messages) > 2:
                raise ValueError("boom")
            return await super().chat(messages, tools)

    _scripted(monkeypatch, CrashOnSecondTurn())
    path = tmp_path / "log.json"
    assert cli.main(["bench", "--model", "m", "--set", "smoke", "--json", str(path)]) == 0
    assert capsys.readouterr().err.splitlines() == ["paging-1 run 1: ValueError"]
    crashed = next(r for r in json.loads(path.read_text(encoding="utf-8")) if r["id"] == "paging-1")
    assert [m["role"] for m in crashed["messages"]] == ["system", "user", "assistant", "tool"]


def test_bench_stops_when_the_backend_is_gone(no_telegram, capsys, monkeypatch):
    calls: list[str] = []

    class Gone(ScriptedBackend):
        async def chat(
            self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
        ) -> ModelTurn:
            calls.append(BY_TEXT[messages[1]["content"]])
            if calls[-1] != "lookup-1":
                raise BackendError(
                    "Ollama is not running at http://127.0.0.1:11434; start it or pass --url."
                )
            return await super().chat(messages, tools)

    _scripted(monkeypatch, Gone())
    assert cli.main(["bench", "--model", "m", "--set", "smoke"]) == 1
    out, err = capsys.readouterr()
    assert _total(out)[1] == "1"  # only the run that finished before the server went away
    assert calls == ["lookup-1", "lookup-1", "history-1"]  # nothing after the failure
    assert err.strip() == "Ollama is not running at http://127.0.0.1:11434; start it or pass --url."


def test_bench_json_to_a_missing_folder(no_telegram, tmp_path, capsys, monkeypatch):
    _scripted(monkeypatch, ScriptedBackend())
    path = tmp_path / "missing" / "log.json"
    assert cli.main(["bench", "--model", "m", "--set", "smoke", "--json", str(path)]) == 1
    out, err = capsys.readouterr()
    assert _total(out)[1] == "5"
    assert err.startswith(f"Cannot write {path}: ")
    assert err.strip().endswith(".")
    assert len(err.strip().splitlines()) == 1


def test_first_cause_unwraps_one_leaf_and_known_groups() -> None:
    leaf = BackendError("x")
    assert cli._first_cause(ExceptionGroup("g", [ExceptionGroup("h", [leaf])])) is leaf
    known = ExceptionGroup("g", [leaf, RemoteRefused("y")])
    assert cli._first_cause(known) is leaf
    mixed = ExceptionGroup("g", [leaf, ValueError("z")])
    assert cli._first_cause(mixed) is mixed


def test_ask_reraises_a_mixed_group(env, monkeypatch):
    mixed = ExceptionGroup("g", [BackendError("x"), ValueError("z")])

    async def run(question: str, backend: Any, server: Any, *, max_steps: int) -> RunResult:
        raise mixed

    _backends(monkeypatch)
    monkeypatch.setattr(cli, "run_question", run)
    with pytest.raises(ExceptionGroup) as raised:
        cli.main(["ask", "q"])
    assert raised.value is mixed


def test_ctrl_c_inside_a_task_group_exits_130(env, capsys, monkeypatch):
    def interrupted(args: Any) -> int:
        raise BaseExceptionGroup("g", [KeyboardInterrupt()])

    monkeypatch.setattr(cli, "_ask_command", interrupted)
    assert cli.main(["ask", "q"]) == 130
