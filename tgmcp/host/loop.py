"""The agent loop: a local model answers one question through the six read-only tools.

The loop talks to the tools through the MCP SDK's in-process `Client`, so the model sees exactly
what Claude Code sees. It stays dialect-free: the assistant message that carried tool calls is
handed back as the backend returned it (`ModelTurn.assistant_message`); only when a backend
gives none (test doubles) does the loop build a neutral one. Tool messages carry both
`tool_call_id` (the OpenAI dialect) and `tool_name` (Ollama); each dialect ignores the other.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, Tool

from tgmcp.host.backends import ChatBackend, ModelTurn, ToolCall
from tgmcp.host.prompt import SYSTEM_PROMPT

HOST_RESULT_CAP = 16_000
CUT_LINE = "[cut by the host: ask with a smaller limit or page with before_id]"
DEFAULT_MAX_STEPS = 8
BAD_TURNS_ALLOWED = 1  # one correction; the second bad turn in a row ends the run
MALFORMED = "_malformed"


@dataclass
class Step:
    call: ToolCall
    result_text: str
    is_error: bool
    cut: bool
    seconds: float


@dataclass
class RunResult:
    answer: str | None
    steps: list[Step]
    finished: bool
    messages: list[dict[str, Any]]
    seconds: float = 0.0


@dataclass
class _Run:
    """Mutable state of one run."""

    messages: list[dict[str, Any]]
    steps: list[Step] = field(default_factory=list)
    last_text: str | None = None
    started: float = field(default_factory=time.perf_counter)

    def result(self, answer: str | None, *, finished: bool) -> RunResult:
        return RunResult(
            answer, self.steps, finished, self.messages, time.perf_counter() - self.started
        )


def mcp_tools_for_model(tools: list[Tool]) -> list[dict[str, Any]]:
    """MCP tool listings in the function-calling shape both dialects accept."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def _full_text(result: CallToolResult) -> str:
    if result.structured_content is not None and not result.is_error:
        return json.dumps(result.structured_content, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(part.text for part in result.content if isinstance(part, TextContent))


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= HOST_RESULT_CAP:
        return text, False
    return f"{text[:HOST_RESULT_CAP]}\n{CUT_LINE}", True


def result_text(result: CallToolResult) -> str:
    """Structured content as compact JSON (else the text parts), capped with `CUT_LINE`."""
    return _cap(_full_text(result))[0]


def _assistant(turn: ModelTurn) -> dict[str, Any]:
    if turn.assistant_message is not None:
        return turn.assistant_message
    message: dict[str, Any] = {"role": "assistant", "content": turn.text}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in turn.tool_calls
        ]
    return message


def _tool_message(call: ToolCall, text: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "tool_name": call.name, "content": text}


async def _run_calls(client: Client, turn: ModelTurn, names: list[str], state: _Run) -> bool:
    """Execute the turn's calls; True when one of them named no tool or had broken arguments."""
    correction = f"Unknown tool or malformed arguments; call one of: {', '.join(names)}."
    bad = False
    for call in turn.tool_calls:
        if call.name not in names or MALFORMED in call.arguments:
            bad = True
            state.steps.append(Step(call, correction, is_error=True, cut=False, seconds=0.0))
            state.messages.append(_tool_message(call, correction))
            continue
        began = time.perf_counter()
        try:
            result = await client.call_tool(call.name, call.arguments)
        except Exception as exc:  # any failure goes back to the model as text
            text, cut, is_error = f"Tool call failed: {type(exc).__name__}.", False, True
        else:
            text, cut = _cap(_full_text(result))
            is_error = bool(result.is_error)
        state.steps.append(Step(call, text, is_error, cut, time.perf_counter() - began))
        state.messages.append(_tool_message(call, text))
    return bad


async def run_question(
    question: str, backend: ChatBackend, server: MCPServer, *, max_steps: int = DEFAULT_MAX_STEPS
) -> RunResult:
    """Ask the model; `max_steps` counts model calls. Tool errors go back to the model as text."""
    state = _Run(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    )
    async with Client(server) as client:
        listed = (await client.list_tools()).tools
        tools = mcp_tools_for_model(listed)
        names = [tool.name for tool in listed]
        bad_turns = 0
        for _ in range(max_steps):
            turn = await backend.chat(state.messages, tools)
            state.messages.append(_assistant(turn))
            if turn.text:
                state.last_text = turn.text
            if not turn.tool_calls:
                return state.result(turn.text, finished=True)
            bad_turns = bad_turns + 1 if await _run_calls(client, turn, names, state) else 0
            if bad_turns > BAD_TURNS_ALLOWED:
                break
    return state.result(state.last_text, finished=False)
