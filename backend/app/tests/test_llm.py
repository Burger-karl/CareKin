"""Tests for the local LLM client (Day 14): app/integrations/llm.py.

Ollama's HTTP API is simulated with httpx.MockTransport, using the response shapes a
real llama3.2:3b on Ollama 0.40 returned. One live test talks to the real model; it
is skipped unless OLLAMA_LIVE=1 (it takes about a minute on CPU).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.integrations import llm
from app.integrations.llm import (
    ChatMessage,
    LLMReply,
    LLMUnavailable,
    MockLLMClient,
    OllamaClient,
    ToolCall,
    ToolSpec,
    text_reply,
    tool_reply,
)

MATCH_TOOL = ToolSpec(
    name="match_caregivers",
    description="Rank bookable caregivers for a patient and time slot",
    parameters={
        "type": "object",
        "properties": {"patient_id": {"type": "string"}},
        "required": ["patient_id"],
    },
)
ASK = [ChatMessage("user", "Find a carer for my son tomorrow morning")]


def ollama_response(message: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The shape Ollama 0.40 returns for a non-streamed /api/chat."""
    body = {
        "model": "llama3.2:3b",
        "message": {"role": "assistant", "content": "", **message},
        "done": True,
        "done_reason": "stop",
        "total_duration": 7_400_000_000,
        "prompt_eval_count": 101,
        "eval_count": 32,
    }
    body.update(extra)
    return body


def client_returning(
    status: int = 200, body: Any = None, *, seen: list[httpx.Request] | None = None
) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return OllamaClient(get_settings(), transport=httpx.MockTransport(handler))


def client_raising(exc: Exception) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return OllamaClient(get_settings(), transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- requests
async def test_sends_a_capped_deterministic_request() -> None:
    seen: list[httpx.Request] = []
    client = client_returning(body=ollama_response({"content": "Hi"}), seen=seen)
    await client.chat(ASK, tools=[MATCH_TOOL], max_tokens=150)

    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert sent["model"] == get_settings().OLLAMA_MODEL
    assert sent["stream"] is False
    assert sent["keep_alive"] == llm.KEEP_ALIVE
    assert sent["options"] == {"temperature": 0, "num_predict": 150, "num_ctx": 4096}
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "match_caregivers",
                "description": MATCH_TOOL.description,
                "parameters": MATCH_TOOL.parameters,
            },
        }
    ]
    assert sent["messages"] == [{"role": "user", "content": ASK[0].content}]


async def test_no_tools_key_without_tools() -> None:
    seen: list[httpx.Request] = []
    await client_returning(body=ollama_response({"content": "Hi"}), seen=seen).chat(ASK)
    assert "tools" not in json.loads(seen[0].content)


def test_tool_turns_are_sent_in_ollamas_format() -> None:
    called = ChatMessage("assistant", "", tool_calls=(ToolCall("match_caregivers", {"a": 1}),))
    result = ChatMessage("tool", '{"results": []}', tool_name="match_caregivers")
    assert called.to_ollama() == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "match_caregivers", "arguments": {"a": 1}}}],
    }
    assert result.to_ollama() == {
        "role": "tool",
        "content": '{"results": []}',
        "tool_name": "match_caregivers",
    }


# ---------------------------------------------------------------- replies
async def test_text_reply() -> None:
    reply = await client_returning(body=ollama_response({"content": "  Two carers fit.\n"})).chat(
        ASK
    )
    assert reply == LLMReply(content="Two carers fit.", prompt_tokens=101, output_tokens=32)


async def test_tool_call_with_object_arguments() -> None:
    body = ollama_response(
        {
            "tool_calls": [
                {
                    "id": "call_y9q7xlqx",
                    "function": {
                        "index": 0,
                        "name": "match_caregivers",
                        "arguments": {"patient_id": "3f2a", "starts_at": "9am"},
                    },
                }
            ]
        }
    )
    reply = await client_returning(body=body).chat(ASK, tools=[MATCH_TOOL])
    assert reply.tool_calls == (
        ToolCall("match_caregivers", {"patient_id": "3f2a", "starts_at": "9am"}),
    )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"patient_id": "3f2a"}', {"patient_id": "3f2a"}),  # a JSON string is accepted
        ("patient 3f2a please", None),  # not JSON: reported back as invalid
        ('["3f2a"]', None),  # JSON, but not an object
    ],
)
async def test_tool_call_arguments_that_arent_an_object(arguments, expected) -> None:
    body = ollama_response(
        {"tool_calls": [{"function": {"name": "match_caregivers", "arguments": arguments}}]}
    )
    reply = await client_returning(body=body).chat(ASK, tools=[MATCH_TOOL])
    assert reply.tool_calls == (ToolCall("match_caregivers", expected),)


async def test_malformed_tool_calls_are_dropped() -> None:
    body = ollama_response({"tool_calls": [{"function": {"arguments": {}}}, "junk"]})
    assert (await client_returning(body=body).chat(ASK)).tool_calls == ()


async def test_a_reply_cut_off_at_max_tokens_is_marked() -> None:
    body = ollama_response({"content": "Chi Okeke is 1.2 km away and"}, done_reason="length")
    assert (await client_returning(body=body).chat(ASK)).truncated is True


# ---------------------------------------------------------------- failures
@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (httpx.ConnectError("refused"), True),  # Ollama container down
        (httpx.ReadTimeout("slow"), True),  # longer than OLLAMA_TIMEOUT_SECONDS
    ],
)
async def test_unreachable_or_slow_ollama(exc, retryable) -> None:
    with pytest.raises(LLMUnavailable) as caught:
        await client_raising(exc).chat(ASK)
    assert caught.value.retryable is retryable
    assert caught.value.code == "llm_unavailable"
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    ("status", "error", "retryable"),
    [
        (404, "model 'llama3.2:3b' not found", False),  # not pulled yet
        (400, "registry.ollama.ai/library/gemma2:2b does not support tools", False),
        (500, "llama runner process has terminated", True),
    ],
)
async def test_ollama_errors(status, error, retryable) -> None:
    with pytest.raises(LLMUnavailable) as caught:
        await client_returning(status, {"error": error}).chat(ASK, tools=[MATCH_TOOL])
    assert caught.value.retryable is retryable
    if status < 500:
        assert error in str(caught.value)


async def test_a_conversation_that_fills_the_context_window_is_refused() -> None:
    body = ollama_response({"content": "..."}, prompt_eval_count=4000)
    with pytest.raises(LLMUnavailable, match="too long"):
        await client_returning(body=body).chat(ASK)


async def test_a_response_without_a_message_is_an_error() -> None:
    with pytest.raises(LLMUnavailable, match="no message"):
        await client_returning(body={"done": True}).chat(ASK)


# ---------------------------------------------------------------- mock and factory
async def test_mock_replays_its_script_and_records_requests() -> None:
    mock = MockLLMClient()
    mock.queue(tool_reply("match_caregivers", {"patient_id": "p1"}), text_reply("Done."))
    first = await mock.chat(ASK, tools=[MATCH_TOOL])
    second = await mock.chat(ASK)
    assert first.tool_calls == (ToolCall("match_caregivers", {"patient_id": "p1"}),)
    assert second.content == "Done."
    assert (await mock.chat(ASK)).content == llm.MOCK_TEXT  # script used up
    assert [r["tools"] for r in mock.requests] == [[MATCH_TOOL], [], []]


async def test_mock_can_script_a_failure() -> None:
    mock = MockLLMClient()
    mock.queue(LLMUnavailable("ollama", "down"))
    with pytest.raises(LLMUnavailable):
        await mock.chat(ASK)


def test_tests_get_the_mock(monkeypatch) -> None:
    assert isinstance(llm.get_llm_client(), MockLLMClient)
    monkeypatch.setattr(get_settings(), "LLM_PROVIDER", "ollama")
    assert isinstance(llm.get_llm_client(), OllamaClient)


def test_production_refuses_a_mocked_llm() -> None:
    with pytest.raises(ValidationError, match="LLM_PROVIDER must not be mock"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="x" * 64,
            FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            CORS_ORIGINS=["https://carekin.example"],
            SMS_PROVIDER="termii",
            SMS_PROVIDER_API_KEY="key",
            SMS_PROVIDER_BASE_URL="https://termii.example",
            LLM_PROVIDER="mock",
        )


# ---------------------------------------------------------------- live
@pytest.mark.skipif(os.environ.get("OLLAMA_LIVE") != "1", reason="set OLLAMA_LIVE=1 to call Ollama")
async def test_live_ollama_calls_a_tool() -> None:
    settings = get_settings().model_copy(
        update={
            "OLLAMA_URL": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            "OLLAMA_TIMEOUT_SECONDS": 180,
        }
    )
    reply = await OllamaClient(settings).chat(
        [ChatMessage("user", "Find caregivers for patient 3f2a")], tools=[MATCH_TOOL]
    )
    assert [call.name for call in reply.tool_calls] == ["match_caregivers"]
