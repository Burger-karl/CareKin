"""Tests: S3 storage helper (core/storage.py). Real boto3 signing, no network."""

from __future__ import annotations

import base64
import json
import uuid
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber

from app.config import Settings, get_settings
from app.core import storage
from app.core.exceptions import ServiceUnavailableError
from app.core.storage import (
    MockStorage,
    S3Storage,
    UnsupportedFileType,
    UploadPurpose,
    new_key,
    owns_key,
)

OWNER = uuid.UUID("2f1c9a52-8d7e-4c1a-9e55-1b7c3f0a6d11")


def s3_storage() -> tuple[S3Storage, object]:
    client = boto3.client(
        "s3",
        region_name="af-south-1",
        aws_access_key_id="AKIATESTTESTTESTTEST",
        aws_secret_access_key="test-secret",
        config=Config(signature_version="s3v4"),
    )
    settings = Settings(_env_file=None, S3_BUCKET="carekin-test", S3_MAX_UPLOAD_BYTES=5_000_000)
    return S3Storage(settings, client=client), client


# ------------------------------------------------------------------ keys
def test_keys_are_generated_never_user_supplied():
    key = new_key(UploadPurpose.CERTIFICATION, OWNER, "application/pdf")
    prefix, owner, name = key.split("/")
    assert prefix == "certifications" and owner == str(OWNER)
    assert name.endswith(".pdf") and len(name) == 36
    assert new_key(UploadPurpose.CERTIFICATION, OWNER, "application/pdf") != key


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml", "application/zip", ""])
def test_risky_types_are_refused(content_type):
    with pytest.raises(UnsupportedFileType):
        new_key(UploadPurpose.CERTIFICATION, OWNER, content_type)


def test_owns_key():
    key = new_key(UploadPurpose.INCIDENT_EVIDENCE, OWNER, "image/jpeg")
    assert owns_key(key, UploadPurpose.INCIDENT_EVIDENCE, OWNER)
    assert not owns_key(key, UploadPurpose.CERTIFICATION, OWNER)  # wrong purpose
    assert not owns_key(key, UploadPurpose.INCIDENT_EVIDENCE, uuid.uuid4())  # someone else's
    for forged in (
        f"incident-evidence/{OWNER}/../../secrets.pdf",
        f"incident-evidence/{OWNER}/x.pdf",
        f"incident-evidence/{OWNER}/{'a' * 32}.exe",
        f"/incident-evidence/{OWNER}/{'a' * 32}.pdf",
    ):
        assert not owns_key(forged, UploadPurpose.INCIDENT_EVIDENCE, OWNER)


# ------------------------------------------------------------------ S3
def test_presigned_post_enforces_type_size_and_encryption():
    store, _ = s3_storage()
    key = new_key(UploadPurpose.CERTIFICATION, OWNER, "application/pdf")
    upload = store.presign_upload(key, "application/pdf")

    assert upload.url.startswith("https://carekin-test.s3")
    assert upload.fields["key"] == key
    assert upload.fields["Content-Type"] == "application/pdf"
    assert upload.fields["x-amz-server-side-encryption"] == "AES256"
    assert upload.fields["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    policy = json.loads(base64.b64decode(upload.fields["policy"]))
    assert ["content-length-range", 1, 5_000_000] in policy["conditions"]
    assert {"Content-Type": "application/pdf"} in policy["conditions"]
    assert {"x-amz-server-side-encryption": "AES256"} in policy["conditions"]
    assert upload.max_bytes == 5_000_000

    with pytest.raises(UnsupportedFileType):
        store.presign_upload(key, "text/html")


def test_presigned_download_is_short_lived_and_names_the_file_safely():
    store, _ = s3_storage()
    url = store.presign_download(
        "passport-exports/x/y.pdf", download_name='Ada "passport" ../../x.pdf'
    )
    query = parse_qs(urlparse(url).query)
    assert query["X-Amz-Expires"] == ["900"]
    disposition = query["response-content-disposition"][0]
    assert disposition.startswith('attachment; filename="')
    assert '"passport"' not in disposition and "/" not in disposition.split("filename=")[1]


async def test_exists_and_delete():
    store, client = s3_storage()
    with Stubber(client) as stub:
        stub.add_response(
            "head_object", {"ContentLength": 1234}, {"Bucket": "carekin-test", "Key": "a/b.pdf"}
        )
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_response("delete_object", {}, {"Bucket": "carekin-test", "Key": "a/b.pdf"})
        assert await store.exists("a/b.pdf") == 1234
        assert await store.exists("a/missing.pdf") is None
        await store.delete("a/b.pdf")
        stub.assert_no_pending_responses()


async def test_other_s3_errors_are_not_swallowed():
    from botocore.exceptions import ClientError

    store, client = s3_storage()
    with Stubber(client) as stub:
        stub.add_client_error(
            "head_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(ClientError):
            await store.exists("a/b.pdf")


# ------------------------------------------------------------------ mock and factory
async def test_mock_storage_round_trip():
    store = MockStorage(max_bytes=10)
    key = new_key(UploadPurpose.CERTIFICATION, OWNER, "image/png")
    upload = store.presign_upload(key, "image/png")
    assert upload.fields["key"] == key
    assert await store.exists(key) is None
    store.put(key, b"png-bytes")
    assert await store.exists(key) == 9
    with pytest.raises(ValueError):
        store.put(key, b"x" * 11)
    assert store.presign_download(key, download_name="licence.png").endswith(
        "?download=licence.png"
    )
    await store.delete(key)
    assert await store.exists(key) is None


def test_get_storage(monkeypatch):
    assert isinstance(storage.get_storage(), MockStorage)
    monkeypatch.setattr(get_settings(), "USE_MOCK_INTEGRATIONS", False)
    monkeypatch.setattr(get_settings(), "S3_BUCKET", None)
    with pytest.raises(ServiceUnavailableError) as exc:
        storage.get_storage()
    assert exc.value.code == "storage_not_configured"
