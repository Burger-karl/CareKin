"""Tests for the agent toolkit's tools and loop (Day 14): agents/tools.py and runner.py.

The model is MockLLMClient. The tools call a small stand-in app built with the real
login dependency and error handlers, so "the agent acts as the user, with a real
token, through the API" is exercised for real, without depending on any one domain.
"""

from __future__ import annotations

import json
import random
from typing import Annotated, Any

import pytest
from cryptography.fernet import Fernet
from fastapi import Body, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.models import AgentChannel, AgentConversation, AgentKind, AgentMessage, MessageRole
from app.agents.runner import ConversationLog, run_agent
from app.agents.tools import AgentTool, ApiCaller, ToolRequestFailed
from app.config import get_settings
from app.core import encryption
from app.core.exceptions import ForbiddenError, register_exception_handlers
from app.core.permissions import UserRole
from app.domains.auth.dependencies import CurrentUser
from app.domains.auth.models import User
from app.integrations.llm import (
    LLMReply,
    LLMUnavailable,
    MockLLMClient,
    ToolCall,
    text_reply,
    tool_reply,
)

SYSTEM = "You find caregivers. Use the tool."


# ----------------------------------------------------------------- stand-in API
stand_in = FastAPI()
register_exception_handlers(stand_in)
PREFIX = get_settings().API_V1_PREFIX


@stand_in.post(PREFIX + "/echo")
async def echo(user: CurrentUser, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Returns who called and what was sent: proves the token and the merged arguments."""
    return {"caller": str(user.id), "role": user.role.value, "body": body, "rows": [1, 2, 3]}


@stand_in.post(PREFIX + "/refuse")
async def refuse(user: CurrentUser) -> None:
    raise ForbiddenError("Only the patient or a guardian can do this", code="patient_forbidden")


class EchoArgs(BaseModel):
    languages: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=10)


ECHO = AgentTool(
    name="find",
    description="Find caregivers.",
    args=EchoArgs,
    method="POST",
    path="/echo",
    for_model=lambda result: {"rows": result["rows"]},
)
REFUSE = AgentTool(
    name="refuse", description="Always refused.", args=EchoArgs, method="POST", path="/refuse"
)
FIXED = {"find": {"patient_id": "the-real-patient", "limit": 3}}


# ----------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def fernet(monkeypatch):
    """Tests don't set FIELD_ENCRYPTION_KEY; use a throwaway key (as test_patients does)."""
    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(encryption, "_fernet", lambda: key)
    return key


@pytest.fixture
def model() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
async def user(db) -> User:
    phone = "+234803" + "".join(random.choices("0123456789", k=7))
    user = User(phone=phone, full_name="Ngozi Okafor", role=UserRole.GUARDIAN)
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
async def log(db, user) -> ConversationLog:
    conversation = AgentConversation(
        agent=AgentKind.MATCHING, user_id=user.id, channel=AgentChannel.WEB
    )
    db.add(conversation)
    await db.commit()
    return await ConversationLog.open(db, conversation)


async def run(model, log, user, *, tools=(ECHO,), required="find", **kwargs):
    return await run_agent(
        llm=model,
        log=log,
        caller=ApiCaller(stand_in, user.id, user.role),
        system=SYSTEM,
        user_text="Someone who speaks Igbo, please",
        tools=list(tools),
        required=required,
        fixed=FIXED,
        **kwargs,
    )


async def logged(db, log) -> list[tuple[MessageRole, str | None, dict | str]]:
    rows = await db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == log.conversation.id)
        .order_by(AgentMessage.position)
    )
    out = []
    for row in rows:
        content = json.loads(row.content) if row.role is MessageRole.TOOL else row.content
        out.append((row.role, row.tool_name, content))
    return out


# ----------------------------------------------------------------- tools
def test_tool_spec_lists_only_what_the_model_may_set() -> None:
    spec = ECHO.spec()
    assert spec.name == "find"
    assert set(spec.parameters["properties"]) == {"languages", "limit"}
    assert spec.parameters["required"] == []


def test_bad_arguments_get_a_message_the_model_can_act_on() -> None:
    with pytest.raises(ValueError, match="limit: Input should be less than or equal to 10"):
        ECHO.validate({"limit": 50})
    with pytest.raises(ValueError, match="arguments must be a JSON object"):
        ECHO.validate(None)


# ----------------------------------------------------------------- the loop
async def test_model_calls_the_tool_then_explains(db, model, log, user) -> None:
    model.queue(tool_reply("find", {"languages": ["igbo"]}), text_reply("Three carers fit."))
    outcome = await run(model, log, user)

    assert outcome.called_by == "model" and outcome.model_calls == 2
    assert outcome.text == "Three carers fit."
    # Acted as the user, through the API, with the fixed arguments merged in.
    assert outcome.result["caller"] == str(user.id) and outcome.result["role"] == "guardian"
    assert outcome.result["body"] == {
        "languages": ["igbo"],
        "limit": 3,
        "patient_id": "the-real-patient",
    }
    assert await logged(db, log) == [
        (MessageRole.USER, None, "Someone who speaks Igbo, please"),
        (
            MessageRole.TOOL,
            "find",
            {
                "called_by": "model",
                "arguments": {"languages": ["igbo"], "limit": 5},
                "result": {"rows": [1, 2, 3]},
            },
        ),
        (MessageRole.ASSISTANT, None, "Three carers fit."),
    ]


async def test_tools_are_offered_only_until_the_data_is_in(model, log, user) -> None:
    model.queue(tool_reply("find"), text_reply("Done."))
    await run(model, log, user)
    first, explanation = model.requests
    assert [t.name for t in first["tools"]] == ["find"]
    assert explanation["tools"] == []
    # The model reads the shortened result, not the whole response.
    assert explanation["messages"][-1].content == '{"rows":[1,2,3]}'


async def test_the_model_cannot_override_fixed_arguments(model, log, user) -> None:
    # Two layers: fields the model may not set are dropped (patient_id), and fixed
    # values win over fields it may set (limit).
    model.queue(tool_reply("find", {"patient_id": "someone-else", "limit": 9}), text_reply("Done."))
    outcome = await run(model, log, user)
    assert outcome.result["body"]["patient_id"] == "the-real-patient"
    assert outcome.result["body"]["limit"] == 3


async def test_bad_arguments_go_back_to_the_model(db, model, log, user) -> None:
    model.queue(
        tool_reply("find", {"limit": "lots"}),
        tool_reply("find", {"limit": 2}),
        text_reply("Two carers."),
    )
    outcome = await run(model, log, user)
    assert outcome.called_by == "model" and outcome.text == "Two carers."
    retry = model.requests[1]["messages"][-1]
    assert retry.role == "tool" and "limit" in retry.content and "integer" in retry.content
    rows = await logged(db, log)
    assert "error" in rows[1][2] and rows[2][2]["called_by"] == "model"


async def test_an_unknown_tool_is_reported_back(model, log, user) -> None:
    model.queue(tool_reply("book_now"), tool_reply("find"), text_reply("Done."))
    await run(model, log, user)
    assert (
        "no tool called 'book_now'; use one of: find" in model.requests[1]["messages"][-1].content
    )


async def test_arguments_that_arent_an_object_are_reported_back(model, log, user) -> None:
    model.queue(
        LLMReply(tool_calls=(ToolCall("find", None),)), tool_reply("find"), text_reply("Done.")
    )
    outcome = await run(model, log, user)
    assert "arguments must be a JSON object" in model.requests[1]["messages"][-1].content
    assert outcome.called_by == "model"


async def test_an_answer_without_the_tool_is_ignored_and_the_code_calls_it(
    db, model, log, user
) -> None:
    model.queue(text_reply("Chi Okeke is perfect for you!"), text_reply("From the list: three."))
    outcome = await run(model, log, user)

    assert outcome.called_by == "code"
    assert outcome.result["body"] == {"limit": 3, "patient_id": "the-real-patient", "languages": []}
    assert outcome.text == "From the list: three."
    rows = await logged(db, log)
    assert all("Chi Okeke" not in str(content) for _, _, content in rows)  # never logged as said
    assert rows[1][2]["called_by"] == "code"
    # The explanation saw the call and its result.
    assert model.requests[1]["messages"][-1].content == '{"rows":[1,2,3]}'


async def test_model_down_still_answers_from_data(db, model, log, user) -> None:
    model.queue(LLMUnavailable("ollama", "unreachable (ConnectError)"))
    outcome = await run(model, log, user)

    assert outcome.called_by == "code" and outcome.result["rows"] == [1, 2, 3]
    assert outcome.text is None and "unreachable" in outcome.llm_error
    assert len(model.requests) == 1  # no explanation attempt once it's down
    assert [role for role, _, _ in await logged(db, log)] == [MessageRole.USER, MessageRole.TOOL]


async def test_running_out_of_turns_skips_the_explanation(model, log, user) -> None:
    model.queue(*[tool_reply("find", {"limit": 99})] * 3)
    outcome = await run(model, log, user)
    assert outcome.called_by == "code" and outcome.text is None and outcome.model_calls == 3


async def test_explanation_failing_leaves_text_empty(model, log, user) -> None:
    model.queue(tool_reply("find"), LLMUnavailable("ollama", "unreachable (ReadTimeout)"))
    outcome = await run(model, log, user)
    assert outcome.called_by == "model" and outcome.result is not None
    assert outcome.text is None and "ReadTimeout" in outcome.llm_error


async def test_a_cut_off_explanation_is_marked(model, log, user) -> None:
    model.queue(tool_reply("find"), LLMReply(content="Chi is near and", truncated=True))
    outcome = await run(model, log, user)
    assert outcome.truncated is True


async def test_a_refused_api_call_stops_the_run_with_its_status(db, model, log, user) -> None:
    model.queue(tool_reply("refuse"))
    with pytest.raises(ToolRequestFailed) as caught:
        await run(model, log, user, tools=(REFUSE,), required="refuse")
    assert caught.value.status_code == 403 and caught.value.code == "patient_forbidden"
    rows = await logged(db, log)
    assert rows[-1][2]["error"] == {"status": 403, "code": "patient_forbidden"}  # still logged


async def test_a_second_run_continues_the_same_conversation(db, model, log, user) -> None:
    model.queue(tool_reply("find"), text_reply("First."), tool_reply("find"), text_reply("Second."))
    await run(model, log, user)
    again = await ConversationLog.open(db, log.conversation)
    await run(model, again, user)
    positions = list(
        await db.scalars(
            select(AgentMessage.position)
            .where(AgentMessage.conversation_id == log.conversation.id)
            .order_by(AgentMessage.position)
        )
    )
    assert positions == list(range(6))
