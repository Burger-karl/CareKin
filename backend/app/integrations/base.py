"""Shared plumbing for partner clients (build plan Day 3).

Every partner sits behind a Protocol with a deterministic mock, picked by a
`get_<name>_client()` factory (also a FastAPI dependency, so tests can override it):

    USE_MOCK_INTEGRATIONS=true    the mock; never leaves the process
    USE_MOCK_INTEGRATIONS=false   the real client, or 503 "<name>_not_configured"
                                  if its keys are missing or no provider is chosen yet

Clients raise IntegrationError when the partner fails or refuses; callers decide
whether to retry. It is a ServiceUnavailableError, so callers can catch either, and if
nobody catches it the API answers 503 "provider_unavailable" instead of a 500.
Clients never log personal data or secrets.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.exceptions import ServiceUnavailableError

# httpx logs every request's full URL at INFO, and some partners take personal data in
# the query string (Dojah: ?nin=...). Keep those lines out of the logs whatever level the
# app or the Celery worker sets.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class IntegrationError(ServiceUnavailableError):
    """A partner call failed (network, HTTP error, unexpected response)."""

    code = "provider_unavailable"

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


def not_configured(name: str, detail: str) -> ServiceUnavailableError:
    return ServiceUnavailableError(
        f"{detail} Set USE_MOCK_INTEGRATIONS=true for local development.",
        code=f"{name}_not_configured",
    )


async def request_json(
    provider: str,
    method: str,
    url: str,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """One HTTP call returning a JSON object, with every failure as IntegrationError."""
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as http:
            response = await http.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise IntegrationError(provider, f"unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 500:
        raise IntegrationError(provider, f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise IntegrationError(
            provider, f"non-JSON response (HTTP {response.status_code})"
        ) from exc
    if not isinstance(data, dict):
        raise IntegrationError(provider, "unexpected response shape")
    if response.status_code >= 400:
        # Client errors won't succeed on retry; the partner's message is safe to surface.
        error = data.get("error")
        nested = error.get("message") if isinstance(error, dict) else error
        message = data.get("message") or nested or "request refused"
        raise IntegrationError(provider, f"HTTP {response.status_code}: {message}", retryable=False)
    return data
