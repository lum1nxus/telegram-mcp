"""Chat backends for local models (Ollama, LM Studio) and the rule that keeps them local.

Plain HTTP through the standard library. `local_only` is the only gate on where a prompt may go:
every backend calls it in `__init__`, and there is no override.
"""

import asyncio
import json
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

Opener = Callable[[urllib.request.Request, float], Any]


class RemoteRefused(Exception):  # noqa: N818 - the name is part of the spec
    """The URL or the model would send the prompt off this computer."""


class BackendError(Exception):
    """One sentence for the user: unreachable, unknown model, bad JSON."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: tuple[ToolCall, ...]
    raw: dict[str, Any]


class ChatBackend(Protocol):
    name: str
    model: str

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...

    async def check_model(self) -> None: ...


def local_only(url: str) -> str:
    """The base URL without a trailing slash, or `RemoteRefused` unless it is this computer."""
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
    return f"http://{netloc}{parts.path.rstrip('/')}"


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


class _Base:
    name = ""
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
            if exc.code == 404 and self.model:  # noqa: PLR2004
                raise BackendError(self._missing(self.model)) from exc
            raise BackendError(f"{self.name} answered HTTP {exc.code} for {path}.") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise BackendError(self.unreachable.format(url=self.url)) from exc


class OllamaBackend(_Base):
    name = "ollama"
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
            raise BackendError("Ollama sent a reply without a message.")
        calls: list[ToolCall] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            function = item.get("function") or {}
            arguments = function.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {"_malformed": json.dumps(arguments, ensure_ascii=False)}
            calls.append(ToolCall(f"call_{index}", str(function.get("name", "")), arguments))
        return ModelTurn(str(message.get("content") or ""), tuple(calls), raw)

    async def check_model(self) -> None:
        if self.model.endswith("-cloud"):
            raise RemoteRefused(_cloud(self.model))
        tags = await self._http("GET", "/api/tags")
        names = [
            str(entry.get("name") or entry.get("model") or "")
            for entry in tags.get("models") or []
            if isinstance(entry, dict)
        ]
        if not self.model:
            if not names:
                raise BackendError("No model is installed; run `ollama pull <model>`.")
            self.model = names[0]
        if self.model.endswith("-cloud"):
            raise RemoteRefused(_cloud(self.model))
        if self.model not in names:
            raise BackendError(self._missing(self.model))
        details = (await self._http("POST", "/api/show", {"model": self.model})).get("details")
        if not isinstance(details, dict) or not (
            _filled(details.get("parameter_size")) and _filled(details.get("quantization_level"))
        ):
            raise RemoteRefused(_cloud(self.model))


class OpenAICompatBackend(_Base):
    name = "lmstudio"
    unreachable = "LM Studio is not running at {url}; start its local server or pass --url."

    def __init__(self, model: str, url: str = LMSTUDIO_URL, *, opener: Opener = _urlopen) -> None:
        super().__init__(model, url, opener)

    def _missing(self, model: str) -> str:
        return f"Model {model} is not loaded in LM Studio; load it there."

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        raw = await self._http(
            "POST",
            "/v1/chat/completions",
            {"model": self.model, "messages": messages, "tools": tools, "temperature": 0},
        )
        try:
            message = raw["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError("LM Studio sent a reply without a message.") from exc
        calls: list[ToolCall] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            function = item.get("function") or {}
            calls.append(
                ToolCall(
                    str(item.get("id") or f"call_{index}"),
                    str(function.get("name", "")),
                    _parse_arguments(function.get("arguments")),
                )
            )
        return ModelTurn(str(message.get("content") or ""), tuple(calls), raw)

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
    return OpenAICompatBackend(model or "", url or LMSTUDIO_URL)
