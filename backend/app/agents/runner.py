"""The agent loop: let the model call tools, log every step, and never depend on it.

One run, for one user message:
1. Log the user's message.
2. Ask the model, offering the tools. Each tool call is validated and run against
   our own API (tools.py); the result, or an error the model can fix, goes back to
   it. At most `max_turns` model calls in total.
3. If the model never gets the `required` tool's result (it answered without calling
   it, kept sending bad arguments, or was unavailable), our code calls that tool
   itself with no model arguments. The agent always answers from real data.
4. Ask the model, without tools, to explain that result. Only text written after the
   result counts: an answer written before it would describe data the model never saw.
5. If the model is down, slow or out of turns, `text` is None and the agent builds its
   reply without the model.

Every step is committed as it happens, so a conversation that fails halfway is still
logged (PRD 8.1). Guardrails and human handoff run around this loop (guardrails.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.models import AgentConversation, AgentMessage, GuardrailFlag, MessageRole
from app.agents.tools import (
    AgentTool,
    ApiCaller,
    ToolArgumentsInvalid,
    ToolRequestFailed,
    compact_json,
)
from app.integrations.llm import ChatMessage, LLMClient, LLMUnavailable, ToolCall

MAX_TURNS = 3  # model calls per run, explanation included: each one costs seconds on CPU
MAX_CALLS_PER_TURN = 2  # tool calls run from one model reply; extras are ignored
EXPLANATION_TOKENS = 200


class ConversationLog:
    """Appends messages to a conversation in order, committing each one."""

    def __init__(self, db: AsyncSession, conversation: AgentConversation, next_position: int):
        self.db = db
        self.conversation = conversation
        self._next = next_position

    @classmethod
    async def open(cls, db: AsyncSession, conversation: AgentConversation) -> ConversationLog:
        last = await db.scalar(
            select(func.max(AgentMessage.position)).where(
                AgentMessage.conversation_id == conversation.id
            )
        )
        return cls(db, conversation, 0 if last is None else last + 1)

    async def add(
        self,
        role: MessageRole,
        content: str,
        *,
        tool_name: str | None = None,
        flag: GuardrailFlag = GuardrailFlag.NONE,
        wa_message_id: str | None = None,
    ) -> AgentMessage:
        message = AgentMessage(
            conversation_id=self.conversation.id,
            position=self._next,
            role=role,
            content=content,
            tool_name=tool_name,
            guardrail_flag=flag,
            wa_message_id=wa_message_id,
        )
        self._next += 1
        self.db.add(message)
        await self.db.commit()
        return message


@dataclass(frozen=True)
class AgentOutcome:
    result: Any  # the required tool's full result (the route's JSON)
    called_by: Literal["model", "code"]  # code: the fallback made the call
    text: str | None  # the model's explanation; None means build the reply without it
    truncated: bool  # the explanation hit its token cap and may end mid-sentence
    llm_error: str | None  # why the model wasn't used, if it wasn't
    model_calls: int


async def run_agent(
    *,
    llm: LLMClient,
    log: ConversationLog,
    caller: ApiCaller,
    system: str,
    user_text: str,
    tools: list[AgentTool],
    required: str,
    fixed: dict[str, dict[str, Any]],
    max_turns: int = MAX_TURNS,
) -> AgentOutcome:
    """Run one user message through the model and tools. See the module docstring.

    `fixed` holds, per tool name, the arguments only our code sets; they override
    anything the model sends. ToolRequestFailed propagates: our own route refused the
    request the code built (e.g. no access to this patient), which no retry can fix.
    """
    by_name = {tool.name: tool for tool in tools}
    specs = [tool.spec() for tool in tools]
    messages = [ChatMessage("system", system), ChatMessage("user", user_text)]
    await log.add(MessageRole.USER, user_text)

    result: Any = None
    called_by: Literal["model", "code"] = "model"
    llm_error: str | None = None
    calls = 0

    async def run_call(call: ToolCall) -> Any:
        """Run one model tool call. Returns the result, or None after an error the model can fix."""
        tool = by_name.get(call.name)
        try:
            if tool is None:
                raise ToolArgumentsInvalid(
                    f"no tool called {call.name!r}; use one of: {', '.join(by_name)}"
                )
            args = tool.validate(call.arguments)
        except ToolArgumentsInvalid as exc:
            error = {"error": str(exc)}
            await log.add(
                MessageRole.TOOL,
                compact_json({"arguments": call.arguments, **error}),
                tool_name=call.name,
            )
            messages.append(ChatMessage("tool", compact_json(error), tool_name=call.name))
            return None
        output = await _run_and_log(log, caller, tool, args, fixed.get(tool.name, {}), "model")
        messages.append(
            ChatMessage("tool", compact_json(tool.for_model(output)), tool_name=tool.name)
        )
        return output

    # Steps 2 and 3: the model gets the data by calling tools.
    while result is None and calls < max_turns:
        try:
            reply = await llm.chat(messages, tools=specs)
        except LLMUnavailable as exc:
            llm_error = str(exc)
            break
        calls += 1
        if not reply.tool_calls:
            break  # it answered without data; that text isn't used
        messages.append(ChatMessage("assistant", reply.content, tool_calls=reply.tool_calls))
        for call in reply.tool_calls[:MAX_CALLS_PER_TURN]:
            output = await run_call(call)
            if output is not None and call.name == required:
                result = output

    if result is None:
        tool = by_name[required]
        args = tool.validate({})  # the tool's model arguments must all be optional
        result = await _run_and_log(log, caller, tool, args, fixed.get(required, {}), "code")
        called_by = "code"
        # Show the model the call, so it explains real data.
        messages.append(ChatMessage("assistant", "", tool_calls=(ToolCall(required, {}),)))
        messages.append(
            ChatMessage("tool", compact_json(tool.for_model(result)), tool_name=required)
        )

    # Step 4: the explanation, written from the result.
    text: str | None = None
    truncated = False
    if llm_error is None and calls < max_turns:
        try:
            reply = await llm.chat(messages, max_tokens=EXPLANATION_TOKENS)
            calls += 1
            text, truncated = reply.content or None, reply.truncated
        except LLMUnavailable as exc:
            llm_error = str(exc)
    if text is not None:
        await log.add(MessageRole.ASSISTANT, text)

    return AgentOutcome(
        result=result,
        called_by=called_by,
        text=text,
        truncated=truncated,
        llm_error=llm_error,
        model_calls=calls,
    )


async def _run_and_log(
    log: ConversationLog,
    caller: ApiCaller,
    tool: AgentTool,
    args: Any,
    fixed: dict[str, Any],
    called_by: Literal["model", "code"],
) -> Any:
    record: dict[str, Any] = {"called_by": called_by, "arguments": args.model_dump(mode="json")}
    try:
        output = await tool.run(caller, args, fixed)
    except ToolRequestFailed as exc:
        await log.add(
            MessageRole.TOOL,
            compact_json({**record, "error": {"status": exc.status_code, "code": exc.code}}),
            tool_name=tool.name,
        )
        raise
    await log.add(
        MessageRole.TOOL,
        compact_json({**record, "result": tool.for_model(output)}),
        tool_name=tool.name,
    )
    return output
