"""Private document storage (certification files, later passport uploads).

Another teammate is building the S3 implementation. This file only defines the
contract the caregivers domain needs, plus an in-memory mock for development
and tests. The S3 class must provide `put()` with the same signature and be
returned from get_document_store() when USE_MOCK_INTEGRATIONS is false.
Files are private: nothing here returns a public URL.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.config import get_settings
from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


class DocumentStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store `data` under `key`. Raises ServiceUnavailableError on failure."""
        ...


class MockDocumentStore:
    """Keeps files in memory. Tests read `files`."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (data, content_type)
        logger.info("Mock document stored: %s (%d bytes)", key, len(data))


_mock_store = MockDocumentStore()


def get_document_store() -> DocumentStore:
    """FastAPI dependency. Tests override it with a fresh MockDocumentStore."""
    if get_settings().USE_MOCK_INTEGRATIONS:
        return _mock_store
    raise ServiceUnavailableError(
        "Document storage is not configured. Set USE_MOCK_INTEGRATIONS=true until S3 is added.",
        code="storage_not_configured",
    )