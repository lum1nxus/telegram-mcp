import asyncio
import http.client
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tgmcp.host.backends import (
    BackendError,
    ModelTurn,
    OllamaBackend,
    OpenAICompatBackend,
    RemoteRefused,
    ToolCall,
    local_only,
    make_backend,
)


@pytest.mark.parametrize(
    ("url", "base"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("http://localhost", "http://localhost"),
        ("HTTP://LocalHost:1234/", "http://localhost:1234"),
        ("http://[::1]:11434/", "http://[::1]:11434"),
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234"),
        ("http://localhost:1/a/b?x=1#f", "http://localhost:1"),
    ],
)
def test_local_only_accepts(url: str, base: str) -> None:
    assert local_only(url) == base


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:11434",
        "http://127.0.0.1.evil.example",
        "http://127.0.0.1@evil.example/",
        "http://evil.example/127.0.0.1",
        "http://localhost.evil.example",
        "https://127.0.0.1:11434",
        "ftp://127.0.0.1",
        "127.0.0.1:11434",
        "http://0.0.0.0:11434",
        "http://ollama.com",
        "",
    ],
)
def test_local_only_refuses(url: str) -> None:
    with pytest.raises(RemoteRefused):
        local_only(url)


def test_remote_refused_names_the_host() -> None:
    with pytest.raises(RemoteRefused, match=r"evil.example is not this computer"):
        local_only("http://127.0.0.1@evil.example/")


class Resp:
    def __init__(self, body: Any) -> None:
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw


class Opener:
    """Records requests and answers from a table keyed by 'METHOD /path'."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, str, Any]] = []
        self.timeout = 0.0

    def __call__(self, request: Any, timeout: float) -> Resp:
        path = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), path, body))
        self.timeout = timeout
        answer = self.answers[f"{request.get_method()} {path}"]
        if isinstance(answer, Exception):
            raise answer
        return Resp(answer)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/", code, "err", None, None)  # type: ignore[arg-type]


TOOLS = [{"type": "function", "function": {"name": "chats", "parameters": {"type": "object"}}}]
LOCAL_SHOW = {"details": {"parameter_size": "12B", "quantization_level": "Q4_K_M"}}


def test_ollama_chat_parses_tool_calls_and_drops_thinking() -> None:
    opener = Opener(
        {
            "POST /api/chat": {
                "message": {
                    "role": "assistant",
                    "content": "ok",
                    "thinking": "secret reasoning",
                    "tool_calls": [
                        {"function": {"name": "search_messages", "arguments": {"query": "лифт"}}}
                    ],
                }
            }
        }
    )
    backend = OllamaBackend("gemma4:12b", opener=opener)
    reply = asyncio.run(backend.chat([{"role": "user", "content": "q"}], TOOLS))
    assert reply.text == "ok"
    assert reply.tool_calls == (ToolCall("call_0", "search_messages", {"query": "лифт"}),)
    method, path, body = opener.requests[0]
    assert (method, path) == ("POST", "/api/chat")
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 16384}
    assert body["tools"] == TOOLS
    assert body["model"] == "gemma4:12b"
    assert opener.timeout == 120


def test_ollama_request_body_is_utf8_not_ascii_escaped() -> None:
    seen: list[bytes] = []

    def opener(request: Any, timeout: float) -> Resp:
        seen.append(request.data)
        return Resp({"message": {"content": "x"}})

    backend = OllamaBackend("m", opener=opener)
    asyncio.run(backend.chat([{"role": "user", "content": "Соседи"}], []))
    assert "Соседи".encode() in seen[0]


def test_ollama_messages_pass_through_and_reply_keeps_raw() -> None:
    opener = Opener({"POST /api/chat": {"message": {"content": "done"}}})
    msgs = [{"role": "tool", "content": "text", "tool_name": "chats"}]
    reply = asyncio.run(OllamaBackend("m", opener=opener).chat(msgs, TOOLS))
    assert reply == ModelTurn("done", (), {"message": {"content": "done"}}, {"content": "done"})
    assert opener.requests[0][2]["messages"] == msgs


def _ollama(show: Any, tags: Any = None, model: str = "gemma4:12b") -> OllamaBackend:
    tags = tags if tags is not None else {"models": [{"name": "gemma4:12b"}]}
    return OllamaBackend(model, opener=Opener({"GET /api/tags": tags, "POST /api/show": show}))


def test_check_model_ok() -> None:
    asyncio.run(_ollama(LOCAL_SHOW).check_model())


def test_check_model_accepts_model_key_in_tags() -> None:
    asyncio.run(_ollama(LOCAL_SHOW, tags={"models": [{"model": "gemma4:12b"}]}).check_model())


def test_check_model_not_installed() -> None:
    backend = _ollama(LOCAL_SHOW, tags={"models": [{"name": "other"}]})
    with pytest.raises(BackendError, match=r"Model gemma4:12b is not installed; run `ollama pull"):
        asyncio.run(backend.check_model())


def test_check_model_cloud_name_refused_before_any_request() -> None:
    opener = Opener({})
    backend = OllamaBackend("gpt-oss:120b-cloud", opener=opener)
    with pytest.raises(RemoteRefused, match="served from the cloud, not from this computer"):
        asyncio.run(backend.check_model())
    assert opener.requests == []


@pytest.mark.parametrize(
    "show",
    [
        {},
        {"details": {}},
        {"details": {"parameter_size": "12B"}},
        {"details": {"parameter_size": "", "quantization_level": "Q4"}},
        {"details": "nope"},
    ],
)
def test_check_model_without_local_details_is_cloud(show: dict[str, Any]) -> None:
    with pytest.raises(RemoteRefused, match="Model gemma4:12b is served from the cloud"):
        asyncio.run(_ollama(show).check_model())


def test_check_model_picks_the_first_installed_when_none_given() -> None:
    opener = Opener(
        {
            "GET /api/tags": {"models": [{"name": "first"}, {"name": "second"}]},
            "POST /api/show": LOCAL_SHOW,
        }
    )
    backend = OllamaBackend("", opener=opener)
    asyncio.run(backend.check_model())
    assert backend.model == "first"
    assert opener.requests[1][2] == {"model": "first"}


def test_no_models_installed() -> None:
    backend = OllamaBackend("", opener=Opener({"GET /api/tags": {"models": []}}))
    with pytest.raises(BackendError, match="No model is installed"):
        asyncio.run(backend.check_model())


def test_unreachable_is_backend_error() -> None:
    opener = Opener({"POST /api/chat": urllib.error.URLError("refused")})
    with pytest.raises(BackendError, match=r"Ollama is not running at http://127.0.0.1:11434"):
        asyncio.run(OllamaBackend("m", opener=opener).chat([], []))


@pytest.mark.parametrize("answer", [TimeoutError(), http_error(500), b"not json", b"[1]"])
def test_timeout_http_error_and_bad_json_are_backend_errors(answer: Any) -> None:
    opener = Opener({"POST /api/chat": answer})
    with pytest.raises(BackendError):
        asyncio.run(OllamaBackend("m", opener=opener).chat([], []))


def test_http_404_on_chat_names_the_model() -> None:
    opener = Opener({"POST /api/chat": http_error(404)})
    with pytest.raises(BackendError, match="Model zzz is not installed"):
        asyncio.run(OllamaBackend("zzz", opener=opener).chat([], []))


def _completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message}]}


def test_openai_parses_string_arguments_and_drops_reasoning() -> None:
    opener = Opener(
        {
            "POST /v1/chat/completions": _completion(
                {
                    "content": "hi",
                    "reasoning_content": "hidden",
                    "reasoning": "hidden",
                    "tool_calls": [
                        {"id": "abc", "function": {"name": "chats", "arguments": '{"limit": 3}'}}
                    ],
                }
            )
        }
    )
    reply = asyncio.run(OpenAICompatBackend("m", opener=opener).chat([], TOOLS))
    assert reply.text == "hi"
    assert reply.tool_calls == (ToolCall("abc", "chats", {"limit": 3}),)
    method, path, body = opener.requests[0]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert body["temperature"] == 0
    assert body["tools"] == TOOLS


def test_openai_flags_malformed_arguments() -> None:
    opener = Opener(
        {
            "POST /v1/chat/completions": _completion(
                {
                    "content": None,
                    "tool_calls": [
                        {"id": "1", "function": {"name": "chats", "arguments": "{oops"}},
                        {"id": "2", "function": {"name": "chats", "arguments": "[1]"}},
                    ],
                }
            )
        }
    )
    reply = asyncio.run(OpenAICompatBackend("m", opener=opener).chat([], TOOLS))
    assert reply.text == ""
    assert reply.tool_calls[0].arguments == {"_malformed": "{oops"}
    assert reply.tool_calls[1].arguments == {"_malformed": "[1]"}


def test_openai_check_model_and_default() -> None:
    opener = Opener({"GET /v1/models": {"data": [{"id": "qwen"}, {"id": "other"}]}})
    asyncio.run(OpenAICompatBackend("qwen", opener=opener).check_model())
    with pytest.raises(BackendError, match="Model zzz is not loaded"):
        asyncio.run(OpenAICompatBackend("zzz", opener=opener).check_model())
    unnamed = OpenAICompatBackend("", opener=opener)
    asyncio.run(unnamed.check_model())
    assert unnamed.model == "qwen"


def test_openai_unreachable() -> None:
    opener = Opener({"POST /v1/chat/completions": urllib.error.URLError("x")})
    with pytest.raises(BackendError, match=r"LM Studio is not running at http://127.0.0.1:1234"):
        asyncio.run(OpenAICompatBackend("m", opener=opener).chat([], []))


def test_constructors_apply_local_only() -> None:
    with pytest.raises(RemoteRefused):
        OllamaBackend("m", "http://10.0.0.5:11434")
    with pytest.raises(RemoteRefused):
        OpenAICompatBackend("m", "http://10.0.0.5:1234")


def test_make_backend() -> None:
    ollama = make_backend("ollama", "m", None, think=True)
    assert isinstance(ollama, OllamaBackend)
    assert (ollama.name, ollama.model) == ("ollama", "m")
    lm = make_backend("lmstudio", None, "http://localhost:1234", think=False)
    assert isinstance(lm, OpenAICompatBackend)
    assert (lm.name, lm.model) == ("lmstudio", "")
    with pytest.raises(RemoteRefused):
        make_backend("ollama", "m", "http://example.com", think=False)


def test_ollama_name_without_tag_matches_latest() -> None:
    backend = _ollama(LOCAL_SHOW, tags={"models": [{"name": "qwen3:latest"}]}, model="qwen3")
    asyncio.run(backend.check_model())
    assert backend.model == "qwen3:latest"


def test_ollama_string_arguments_are_parsed_and_malformed_flagged() -> None:
    opener = Opener(
        {
            "POST /api/chat": {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "a", "arguments": '{"x": 1}'}},
                        {"function": {"name": "b", "arguments": "{oops"}},
                    ],
                }
            }
        }
    )
    turn = asyncio.run(OllamaBackend("m", opener=opener).chat([], TOOLS))
    assert turn.tool_calls[0].arguments == {"x": 1}
    assert turn.tool_calls[1].arguments == {"_malformed": "{oops"}


@pytest.mark.parametrize(
    "answer",
    [
        {"message": "text"},
        {"message": {"tool_calls": "x"}},
        {"message": {"tool_calls": ["x"]}},
        {"message": {"tool_calls": [{"function": 3}]}},
    ],
)
def test_ollama_unreadable_answers(answer: dict[str, Any]) -> None:
    opener = Opener({"POST /api/chat": answer})
    with pytest.raises(BackendError, match="Ollama returned an answer tgmcp cannot read"):
        asyncio.run(OllamaBackend("m", opener=opener).chat([], []))


@pytest.mark.parametrize(
    "answer",
    [{"choices": [{"message": "x"}]}, {"choices": [{"message": {"tool_calls": [1]}}]}],
)
def test_openai_unreadable_answers(answer: dict[str, Any]) -> None:
    opener = Opener({"POST /v1/chat/completions": answer})
    with pytest.raises(BackendError, match="LM Studio returned an answer tgmcp cannot read"):
        asyncio.run(OpenAICompatBackend("m", opener=opener).chat([], []))


def test_incomplete_read_is_a_transport_error() -> None:
    opener = Opener({"POST /api/chat": http.client.IncompleteRead(b"")})
    with pytest.raises(BackendError, match="Ollama is not running at"):
        asyncio.run(OllamaBackend("m", opener=opener).chat([], []))


def test_leading_think_block_is_stripped_in_both_dialects() -> None:
    think = "<think>\nhmm\n</think>\nAnswer"
    o = Opener({"POST /api/chat": {"message": {"content": think}}})
    assert asyncio.run(OllamaBackend("m", opener=o).chat([], [])).text == "Answer"
    c = Opener({"POST /v1/chat/completions": _completion({"content": "<think>x</think> Hi <b>"})})
    assert asyncio.run(OpenAICompatBackend("m", opener=c).chat([], [])).text == "Hi <b>"


def test_404_on_listing_means_not_running_but_on_chat_means_missing_model() -> None:
    tags = Opener({"GET /api/tags": http_error(404)})
    with pytest.raises(BackendError, match="Ollama is not running at"):
        asyncio.run(OllamaBackend("m", opener=tags).check_model())
    models = Opener({"GET /v1/models": http_error(404)})
    with pytest.raises(BackendError, match="LM Studio is not running at"):
        asyncio.run(OpenAICompatBackend("m", opener=models).check_model())
    chat = Opener({"POST /v1/chat/completions": http_error(404)})
    with pytest.raises(BackendError, match="is not loaded in LM Studio"):
        asyncio.run(OpenAICompatBackend("m", opener=chat).chat([], []))


def test_default_opener_ignores_proxies_and_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    try:
        backend = OllamaBackend("m", f"http://127.0.0.1:{server.server_address[1]}")
        with pytest.raises(BackendError, match="HTTP 302"):
            asyncio.run(backend.check_model())
    finally:
        server.shutdown()
        server.server_close()


def test_assistant_message_is_the_original_message_in_both_dialects() -> None:
    ollama_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "list_chats", "arguments": {}}}],
    }
    opener = Opener({"POST /api/chat": {"message": ollama_message}})
    turn = asyncio.run(OllamaBackend("m", opener=opener).chat([], TOOLS))
    assert turn.assistant_message == ollama_message
    openai_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "x1", "type": "function", "function": {"name": "get_chat", "arguments": "{}"}}
        ],
    }
    opener = Opener({"POST /v1/chat/completions": _completion(openai_message)})
    turn = asyncio.run(OpenAICompatBackend("m", opener=opener).chat([], TOOLS))
    assert turn.assistant_message == openai_message


def test_model_turn_without_assistant_message_defaults_to_none() -> None:
    assert ModelTurn("t", (), {}).assistant_message is None
