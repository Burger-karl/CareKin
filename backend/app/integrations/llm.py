"""Local LLM client (Ollama) for the agent toolkit (Day 14, PRD section 8).

Ollama runs in docker-compose, so health data never leaves our servers. The agents
use one call: `chat()`, which returns either text or tool calls. The model never
touches the database; the agent loop runs each tool call against our own REST API.

Picked by LLM_PROVIDER, not USE_MOCK_INTEGRATIONS: locally the partners are mocked
but the model is real.
    ollama   the real model at OLLAMA_URL
    mock     scripted replies (MockLLMClient); tests only, refused in production

Small CPU models are slow (llama3.2:3b here: ~14 prompt tokens/s, ~3.6 output
tokens/s, plus ~16 s to load when cold), so:
- `max_tokens` caps every reply: output length is most of the wait.
- KEEP_ALIVE keeps the model loaded between requests.
- Every failure (unreachable, timeout, model missing, no tool support, a context
  window too small for the conversation) raises LLMUnavailable, so the agent can
  fall back to a reply built without the model instead of failing the request.

Prompts and replies may hold health data: they are never logged. Only timings and
token counts are.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.config import Settings, get_settings
from app.integrations.base import IntegrationError, request_json

logger = logging.getLogger(__name__)

PROVIDER = "ollama"
DEFAULT_MAX_TOKENS = 200  # ~1 minute of output on CPU; agents keep replies short
CONTEXT_TOKENS = 4096  # agent prompts are small; this keeps memory use low
KEEP_ALIVE = "30m"  # unload after this long idle (loading costs ~16 s)
# Above this share of the context window, Ollama may have silently dropped input.
_CONTEXT_SAFETY = 0.95

Role = Literal["system", "user", "assistant", "tool"]


class LLMUnavailable(IntegrationError):
    """The model couldn't answer. Agents catch this and reply without the model."""

    code = "llm_unavailable"


# ---------------------------------------------------------------- messages
@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    # None when the model sent arguments that aren't a JSON object; the agent loop
    # reports that back to the model as an error.
    arguments: dict[str, Any] | None


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()  # an assistant turn that called tools
    tool_name: str | None = None  # a tool turn: which tool this result is from

    def to_ollama(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments or {}}}
                for call in self.tool_calls
            ]
        if self.tool_name:
            message["tool_name"] = self.tool_name
        return message


@dataclass(frozen=True)
class LLMReply:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    truncated: bool = False  # stopped at max_tokens: the text may end mid-sentence
    prompt_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    model: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMReply: ...


# ---------------------------------------------------------------- parsing
def _tool_call(raw: Any) -> ToolCall | None:
    function = raw.get("function") if isinstance(raw, dict) else None
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        return None
    arguments = function.get("arguments")
    if isinstance(arguments, str):  # some models send a JSON string, not an object
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    return ToolCall(function["name"], arguments if isinstance(arguments, dict) else None)


def parse_reply(data: dict[str, Any]) -> LLMReply:
    message = data.get("message")
    if not isinstance(message, dict):
        raise LLMUnavailable(PROVIDER, "response has no message")
    content = message.get("content")
    calls = [_tool_call(raw) for raw in message.get("tool_calls") or []]
    return LLMReply(
        content=content.strip() if isinstance(content, str) else "",
        tool_calls=tuple(call for call in calls if call is not None),
        truncated=data.get("done_reason") == "length",
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
    )


# ---------------------------------------------------------------- clients
class OllamaClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.model = settings.OLLAMA_MODEL
        self._url = settings.OLLAMA_URL.rstrip("/") + "/api/chat"
        self._timeout = settings.OLLAMA_TIMEOUT_SECONDS
        self._transport = transport

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_ollama() for m in messages],
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            # temperature 0: the same conversation gets (nearly) the same reply.
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": CONTEXT_TOKENS},
        }
        if tools:
            payload["tools"] = [tool.to_ollama() for tool in tools]
        try:
            data = await request_json(
                PROVIDER,
                "POST",
                self._url,
                timeout=self._timeout,
                transport=self._transport,
                json=payload,
            )
        except LLMUnavailable:
            raise
        except IntegrationError as exc:
            # Unreachable, timed out, model not pulled, model without tool support...
            raise LLMUnavailable(
                PROVIDER, str(exc).removeprefix(f"{PROVIDER}: "), retryable=exc.retryable
            ) from exc
        if int(data.get("prompt_eval_count") or 0) >= CONTEXT_TOKENS * _CONTEXT_SAFETY:
            raise LLMUnavailable(
                PROVIDER, "conversation too long for the context window", retryable=False
            )
        reply = parse_reply(data)
        logger.info(
            "LLM reply: %d prompt tokens, %d output tokens, %d tool calls, %.1f s",
            reply.prompt_tokens,
            reply.output_tokens,
            len(reply.tool_calls),
            int(data.get("total_duration") or 0) / 1e9,
        )
        return reply


@dataclass
class MockLLMClient:
    """Replays scripted replies in order; records every request.

    Script an LLMReply to return it, or an exception to raise it. With the script
    empty it answers MOCK_TEXT, so a forgotten script shows up in assertions.
    """

    model: str = "mock"
    script: deque[LLMReply | Exception] = field(default_factory=deque)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def queue(self, *replies: LLMReply | Exception) -> None:
        self.script.extend(replies)

    def reset(self) -> None:
        self.script.clear()
        self.requests.clear()

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMReply:
        self.requests.append(
            {"messages": list(messages), "tools": list(tools or []), "max_tokens": max_tokens}
        )
        if not self.script:
            return LLMReply(content=MOCK_TEXT)
        next_reply = self.script.popleft()
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


MOCK_TEXT = "(mock LLM reply)"


def text_reply(content: str) -> LLMReply:
    """A scripted reply with text and no tool calls (for tests)."""
    return LLMReply(content=content)


def tool_reply(name: str, arguments: dict[str, Any] | None = None) -> LLMReply:
    """A scripted reply that calls one tool (for tests)."""
    return LLMReply(tool_calls=(ToolCall(name, arguments if arguments is not None else {}),))


_mock_client = MockLLMClient()


def get_llm_client() -> LLMClient:
    """FastAPI dependency."""
    settings = get_settings()
    if settings.LLM_PROVIDER == "mock":
        return _mock_client
    return OllamaClient(settings)
