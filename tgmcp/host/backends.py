"""Chat backends for local models (Ollama, LM Studio) and the rule that keeps them local.

Plain HTTP through the standard library. `local_only` is the only gate on where a prompt may go:
every backend calls it in `__init__`, and there is no override.
"""

import asyncio
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

REQUEST_TIMEOUT_S = 120.0
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
OLLAMA_URL = "http://127.0.0.1:11434"
LMSTUDIO_URL = "http://127.0.0.1:1234"
DEFAULT_NUM_CTX = 16384
NOT_FOUND = 404
_MODEL_PATHS = frozenset({"/api/chat", "/api/show", "/v1/chat/completions"})
_REASONING_KEYS = frozenset({"thinking", "reasoning", "reasoning_content"})
_THINK_SPAN = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)
_REMOTE_KEYS = ("remote_host", "remote_model")  # Ollama's marks of a model served elsewhere
_ERROR_TEXT_MAX = 300
TIMED_OUT = (
    f"The model did not answer within {REQUEST_TIMEOUT_S:.0f} s; "
    "try a smaller model, --max-steps, or a shorter question."
)

Opener = Callable[[urllib.request.Request, float], Any]


class RemoteRefused(Exception):  # noqa: N818 - the name is part of the spec
    """The URL or the model would send the prompt off this computer."""


class BackendError(Exception):
    """One sentence for the user: unreachable, unknown model, bad JSON."""


class BackendUnreachable(BackendError):  # noqa: N818 - a kind of BackendError
    """The model server is not running at its address: `tgmcp bench` stops on it."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    """One model answer. `assistant_message` is the backend's own assistant message, in its
    dialect, so the loop can hand it back unchanged on the next call."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    raw: dict[str, Any]
    assistant_message: dict[str, Any] | None = None


class ChatBackend(Protocol):
    name: str
    model: str

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...

    async def check_model(self) -> None: ...


def local_only(url: str) -> str:
    """Scheme, host and port only (path, query and fragment dropped), or `RemoteRefused`."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as exc:
        raise RemoteRefused(
            f"tgmcp talks to local models only; {url!r} is not a valid URL."
        ) from exc
    if parts.scheme.lower() != "http" or host is None or host.lower() not in LOCAL_HOSTS:
        shown = host or url
        raise RemoteRefused(f"tgmcp talks to local models only; {shown} is not this computer.")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        netloc += f":{parts.port}"
    return f"http://{netloc}"


class _Body:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A local server must not bounce a prompt to another host."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        return None


_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _urlopen(request: urllib.request.Request, timeout: float) -> _Body:
    with _LOCAL_OPENER.open(request, timeout=timeout) as response:
        return _Body(response.read())


def _http_json(
    opener: Opener, method: str, url: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    raw = opener(request, REQUEST_TIMEOUT_S).read()
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise BackendError(f"The model server at {url} sent a reply that is not JSON.") from exc
    if not isinstance(parsed, dict):
        raise BackendError(f"The model server at {url} sent an unexpected reply.")
    return parsed


def _error_text(exc: urllib.error.HTTPError) -> str:
    """The `error` field of the local server's JSON reply, or nothing."""
    try:
        parsed = json.loads(exc.read() or b"null")
    except (OSError, ValueError, AttributeError):
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):  # the OpenAI dialect: {"error": {"message": ...}}
        error = error.get("message")
    if not isinstance(error, str):
        return ""
    text = " ".join(error.split()).rstrip(".")
    return text[:_ERROR_TEXT_MAX]


class _Base:
    name = ""
    label = ""  # the name in sentences for the user
    unreachable = ""

    def __init__(self, model: str, url: str, opener: Opener) -> None:
        self.model = model
        self.url = local_only(url)
        self._opener = opener

    def _missing(self, model: str) -> str:
        raise NotImplementedError

    async def _http(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(_http_json, self._opener, method, self.url + path, body)
        except urllib.error.HTTPError as exc:
            if exc.code == NOT_FOUND and path in _MODEL_PATHS and self.model:
                raise BackendError(self._missing(self.model)) from exc
            if exc.code == NOT_FOUND:
                raise BackendUnreachable(self.unreachable.format(url=self.url)) from exc
            detail = _error_text(exc)
            said = f": {detail}" if detail else ""
            raise BackendError(f"{self.label} answered HTTP {exc.code} for {path}{said}.") from exc
        except TimeoutError as exc:  # the server is there but the model is too slow
            raise BackendError(TIMED_OUT) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise BackendError(TIMED_OUT) from exc
            raise BackendUnreachable(self.unreachable.format(url=self.url)) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            raise BackendUnreachable(self.unreachable.format(url=self.url)) from exc


class OllamaBackend(_Base):
    name = "ollama"
    label = "Ollama"
    unreachable = "Ollama is not running at {url}; start it or pass --url."

    def __init__(
        self,
        model: str,
        url: str = OLLAMA_URL,
        *,
        think: bool = False,
        num_ctx: int = DEFAULT_NUM_CTX,
        opener: Opener = _urlopen,
    ) -> None:
        super().__init__(model, url, opener)
        self.think = think
        self.num_ctx = num_ctx

    def _missing(self, model: str) -> str:
        return f"Model {model} is not installed; run `ollama pull {model}`."

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        raw = await self._http(
            "POST",
            "/api/chat",
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": self.think,
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
            },
        )
        message = raw.get("message")
        if not isinstance(message, dict):
            raise BackendError(_UNREADABLE.format(name="Ollama"))
        calls = _tool_calls(message.get("tool_calls"), "Ollama", ids=False)
        return ModelTurn(_text(message), calls, raw, _echo(message))

    async def check_model(self) -> None:
        if self.model.endswith("-cloud"):
            raise RemoteRefused(_cloud(self.model))
        tags = await self._http("GET", "/api/tags")
        entries = [entry for entry in tags.get("models") or [] if isinstance(entry, dict)]
        names = [str(entry.get("name") or entry.get("model") or "") for entry in entries]
        if not self.model:
            if not names:
                raise BackendError("No model is installed; run `ollama pull <model>`.")
            self.model = names[0]
        if self.model.endswith("-cloud"):
            raise RemoteRefused(_cloud(self.model))
        if self.model not in names:
            if f"{self.model}:latest" not in names:
                raise BackendError(self._missing(self.model))
            self.model = f"{self.model}:latest"
        listed = entries[names.index(self.model)]
        shown = await self._http("POST", "/api/show", {"model": self.model})
        if _remote(listed) or _remote(shown):
            raise RemoteRefused(_cloud(self.model))
        details = shown.get("details")
        if not isinstance(details, dict) or not (
            _filled(details.get("parameter_size")) and _filled(details.get("quantization_level"))
        ):
            raise RemoteRefused(_cloud(self.model))
        capabilities = shown.get("capabilities")
        if isinstance(capabilities, list) and "tools" not in capabilities:
            raise BackendError(
                f"Model {self.model} cannot call tools; pick one from docs/local-models.md."
            )


class OpenAICompatBackend(_Base):
    name = "lmstudio"
    label = "LM Studio"
    unreachable = "LM Studio is not running at {url}; start its local server or pass --url."

    def __init__(self, model: str, url: str = LMSTUDIO_URL, *, opener: Opener = _urlopen) -> None:
        super().__init__(model, url, opener)

    def _missing(self, model: str) -> str:
        return f"Model {model} is not available in LM Studio (download or load it)."

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        raw = await self._http(
            "POST",
            "/v1/chat/completions",
            {"model": self.model, "messages": messages, "tools": tools, "temperature": 0},
        )
        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError(_UNREADABLE.format(name="LM Studio")) from exc
        if not isinstance(message, dict):
            raise BackendError(_UNREADABLE.format(name="LM Studio"))
        calls = _tool_calls(message.get("tool_calls"), "LM Studio", ids=True)
        return ModelTurn(_text(message), calls, raw, _echo(message))

    async def check_model(self) -> None:
        listed = await self._http("GET", "/v1/models")
        ids = [
            str(entry.get("id", ""))
            for entry in listed.get("data") or []
            if isinstance(entry, dict)
        ]
        if not self.model:
            if not ids:
                raise BackendError("No model is loaded in LM Studio; load one there.")
            self.model = ids[0]
        if self.model not in ids:
            raise BackendError(self._missing(self.model))


_UNREADABLE = "{name} returned an answer tgmcp cannot read."


def _text(message: dict[str, Any]) -> str:
    """The plain content: reasoning fields are never read, a leading <think> span is cut."""
    return _THINK_SPAN.sub("", str(message.get("content") or ""), count=1)


def _echo(message: dict[str, Any]) -> dict[str, Any]:
    """The assistant message to hand back: reasoning fields dropped, a leading <think> span cut,
    so the history does not grow with the model's thinking."""
    kept = {key: value for key, value in message.items() if key not in _REASONING_KEYS}
    if isinstance(kept.get("content"), str):
        kept["content"] = _text(message)
    return kept


def _tool_calls(items: Any, name: str, *, ids: bool) -> tuple[ToolCall, ...]:
    if items is None:
        return ()
    if not isinstance(items, list):
        raise BackendError(_UNREADABLE.format(name=name))
    calls: list[ToolCall] = []
    for index, item in enumerate(items):
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            raise BackendError(_UNREADABLE.format(name=name))
        call_id = str(item.get("id") or f"call_{index}") if ids else f"call_{index}"
        calls.append(
            ToolCall(
                call_id, str(function.get("name", "")), _parse_arguments(function.get("arguments"))
            )
        )
    return tuple(calls)


def _remote(entry: dict[str, Any]) -> bool:
    """Ollama names a remote host or model only for a model it does not run itself."""
    return any(key in entry for key in _REMOTE_KEYS)


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _cloud(model: str) -> str:
    return f"Model {model} is served from the cloud, not from this computer."


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    try:
        parsed = json.loads(text) if text.strip() else {}
    except ValueError:
        return {"_malformed": text}
    return parsed if isinstance(parsed, dict) else {"_malformed": text}


def make_backend(
    kind: Literal["ollama", "lmstudio"], model: str | None, url: str | None, *, think: bool
) -> ChatBackend:
    if kind == "ollama":
        return OllamaBackend(model or "", url or OLLAMA_URL, think=think)
    if think:
        raise BackendError("--think is only available with --backend ollama.")
    return OpenAICompatBackend(model or "", url or LMSTUDIO_URL)
