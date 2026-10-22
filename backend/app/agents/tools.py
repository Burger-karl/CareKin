"""Agent tools: how an agent acts. Agents call the CareKin REST API, never the database.

PRD 8.1: "Agents call the project's own REST API with tool calling and never access
the database directly." Each tool is one HTTP request to one of our own routes, made
in-process (httpx ASGITransport, no network hop) as the user the agent is acting for,
with a short-lived access token. So the route's role checks, access rules, validation
and audit log apply to the agent exactly as they do to the app.

A tool has two kinds of arguments:
- `Args`: what the model may choose (e.g. preferred languages). Validated with
  Pydantic; a bad value goes back to the model as an error so it can try again.
- `fixed`: what only our code sets (e.g. the patient and the time slot, taken from
  the request). Merged in last, so the model can never change them. A small model
  also gets these wrong: llama3.2:3b sent "9am" as a timestamp.

A route answering 4xx or 5xx raises ToolRequestFailed with the route's own status and
error code: the request the code built was refused, which the model can't fix.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from starlette.types import ASGIApp

from app.config import get_settings
from app.core.exceptions import CareKinError
from app.core.permissions import UserRole
from app.core.security import create_access_token
from app.integrations.llm import ToolSpec

# Long enough for one agent request, short enough to be useless if it leaked.
TOOL_TOKEN_LIFETIME = timedelta(minutes=5)
_INTERNAL_BASE_URL = "http://carekin.internal"


class ToolRequestFailed(CareKinError):
    """A tool's API call was refused. Carries that route's status and error code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code


@dataclass
class ApiCaller:
    """Calls our own API in-process as one user."""

    app: ASGIApp
    user_id: uuid.UUID
    role: UserRole

    async def request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        token, _ = create_access_token(
            self.user_id, self.role.value, expires_delta=TOOL_TOKEN_LIFETIME
        )
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url=_INTERNAL_BASE_URL + get_settings().API_V1_PREFIX
        ) as http:
            response = await http.request(
                method, path, json=json_body, headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code >= 400:
            try:
                error = response.json()["error"]
                code, message = str(error["code"]), str(error["message"])
            except (ValueError, KeyError, TypeError):
                code, message = "tool_request_failed", f"HTTP {response.status_code}"
            raise ToolRequestFailed(response.status_code, code, message)
        return response.json()


@dataclass(frozen=True)
class AgentTool:
    """One action an agent may take: a route on our own API."""

    name: str
    description: str  # what the model reads to decide when to use it
    args: type[BaseModel]  # what the model may set
    method: str
    path: str
    # Shortens the route's JSON for the model's context; the full result is kept for
    # the API response. Small models read slowly (~14 tokens/s here).
    for_model: Callable[[Any], Any] = field(default=lambda result: result)
    # The JSON Schema shown to the model. Defaults to `args`' schema; give a flat one
    # by hand when `args` uses enums or other $refs, which small models handle poorly.
    parameters: dict[str, Any] | None = None

    def spec(self) -> ToolSpec:
        if self.parameters is not None:
            return ToolSpec(self.name, self.description, self.parameters)
        schema = self.args.model_json_schema()
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        )

    def validate(self, arguments: dict[str, Any] | None) -> BaseModel:
        """The model's arguments, checked. Raises ToolArgumentsInvalid with a fixable message."""
        if arguments is None:
            raise ToolArgumentsInvalid("arguments must be a JSON object")
        try:
            return self.args.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolArgumentsInvalid(problems) from exc

    async def run(self, caller: ApiCaller, args: BaseModel, fixed: dict[str, Any]) -> Any:
        body = {**args.model_dump(mode="json", exclude_none=True), **fixed}
        return await caller.request(self.method, self.path, json_body=body)


class ToolArgumentsInvalid(ValueError):
    """The model's arguments don't fit the tool. Sent back to the model to fix."""


def compact_json(value: Any) -> str:
    """JSON without spaces: fewer tokens for a slow model to read."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
