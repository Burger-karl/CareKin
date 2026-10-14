"""File storage on S3: certifications (Day 6), passport exports (Day 8), incident evidence.

Files never pass through the API. The client asks for a presigned upload, sends the
file straight to S3, then tells the API the key, which the API checks with
`owns_key()` and `exists()` before saving it on a record:

    storage = get_storage()
    key = new_key(UploadPurpose.CERTIFICATION, caregiver.id, "application/pdf")
    upload = storage.presign_upload(key, "application/pdf")   # -> url + form fields
    ... later, when the client confirms ...
    if not owns_key(key, UploadPurpose.CERTIFICATION, caregiver.id) or not await storage.exists(key):
        raise ...

Rules this module enforces:
- Keys are generated here (`<purpose>/<owner id>/<random>.<ext>`). User file names
  never become keys, so they can't collide, traverse or leak names.
- Only PDF, JPEG, PNG and WebP, up to S3_MAX_UPLOAD_BYTES; S3 itself enforces the
  type and size through the presigned POST policy.
- Objects are encrypted at rest (SSE-S3) and the bucket must block public access;
  reads go through short-lived presigned URLs only.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import quote

from app.config import Settings, get_settings
from app.integrations.base import not_configured

ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class UploadPurpose(StrEnum):
    CERTIFICATION = "certifications"
    PASSPORT_EXPORT = "passport-exports"
    INCIDENT_EVIDENCE = "incident-evidence"


class UnsupportedFileType(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """POST `fields` plus the file (as the last form field named "file") to `url`."""

    key: str
    url: str
    fields: dict[str, str]
    content_type: str
    max_bytes: int
    expires_at: datetime


def new_key(purpose: UploadPurpose, owner_id: uuid.UUID, content_type: str) -> str:
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if ext is None:
        raise UnsupportedFileType(
            f"Upload a PDF, JPEG, PNG or WebP file (got {content_type or 'unknown type'})"
        )
    return f"{purpose.value}/{owner_id}/{uuid.uuid4().hex}.{ext}"


def owns_key(key: str, purpose: UploadPurpose, owner_id: uuid.UUID) -> bool:
    """True if `key` is one this module would generate for this owner and purpose."""
    pattern = rf"^{re.escape(purpose.value)}/{re.escape(str(owner_id))}/[0-9a-f]{{32}}\.(pdf|jpg|png|webp)$"
    return re.match(pattern, key) is not None


def _download_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name).strip("-.") or "download"
    return cleaned[:100]


class StorageClient(Protocol):
    def presign_upload(self, key: str, content_type: str) -> PresignedUpload: ...

    def presign_download(self, key: str, *, download_name: str | None = None) -> str: ...

    async def exists(self, key: str) -> int | None:
        """Size in bytes if the object exists, else None."""
        ...

    async def delete(self, key: str) -> None: ...


class S3Storage:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.S3_BUCKET:
            raise ValueError("S3_BUCKET is not set")
        self.bucket = settings.S3_BUCKET
        self.expiry = settings.S3_PRESIGN_EXPIRY_SECONDS
        self.max_bytes = settings.S3_MAX_UPLOAD_BYTES
        self._client = client or _boto3_client(settings)

    def presign_upload(self, key: str, content_type: str) -> PresignedUpload:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedFileType("Unsupported file type")
        post = self._client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type, "x-amz-server-side-encryption": "AES256"},
            Conditions=[
                {"Content-Type": content_type},
                {"x-amz-server-side-encryption": "AES256"},
                ["content-length-range", 1, self.max_bytes],
            ],
            ExpiresIn=self.expiry,
        )
        return PresignedUpload(
            key=key,
            url=post["url"],
            fields=post["fields"],
            content_type=content_type,
            max_bytes=self.max_bytes,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.expiry),
        )

    def presign_download(self, key: str, *, download_name: str | None = None) -> str:
        params: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{_download_name(download_name)}"'
            )
        return self._client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=self.expiry
        )

    async def exists(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            head = await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return int(head["ContentLength"])

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)


def _boto3_client(settings: Settings) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        # None falls back to the standard AWS credential chain (e.g. an IAM role).
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=(
            settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
            if settings.AWS_SECRET_ACCESS_KEY
            else None
        ),
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


@dataclass
class MockStorage:
    """In-memory stand-in. `put()` simulates the client's direct upload."""

    max_bytes: int = 10 * 1024 * 1024
    objects: dict[str, bytes] = field(default_factory=dict)
    base_url: str = "https://storage.mock.carekin.invalid"

    def presign_upload(self, key: str, content_type: str) -> PresignedUpload:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UnsupportedFileType("Unsupported file type")
        return PresignedUpload(
            key=key,
            url=f"{self.base_url}/upload",
            fields={"key": key, "Content-Type": content_type},
            content_type=content_type,
            max_bytes=self.max_bytes,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    def presign_download(self, key: str, *, download_name: str | None = None) -> str:
        url = f"{self.base_url}/{quote(key)}"
        return f"{url}?download={quote(_download_name(download_name))}" if download_name else url

    def put(self, key: str, data: bytes) -> None:
        if not 0 < len(data) <= self.max_bytes:
            raise ValueError("File is empty or too large")
        self.objects[key] = data

    async def exists(self, key: str) -> int | None:
        data = self.objects.get(key)
        return len(data) if data is not None else None

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@lru_cache
def _mock_storage() -> MockStorage:
    return MockStorage(max_bytes=get_settings().S3_MAX_UPLOAD_BYTES)


@lru_cache
def _s3_storage() -> S3Storage:
    return S3Storage(get_settings())


def get_storage() -> StorageClient:
    """FastAPI dependency. Mock while USE_MOCK_INTEGRATIONS is true."""
    settings = get_settings()
    if settings.USE_MOCK_INTEGRATIONS:
        return _mock_storage()
    if not settings.S3_BUCKET:
        raise not_configured("storage", "File uploads need S3_BUCKET.")
    return _s3_storage()
