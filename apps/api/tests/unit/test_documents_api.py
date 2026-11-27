"""HTTP contract of the document and search endpoints, without a database.

Everything asserted here is decided before the database is touched: size limits,
file validation, admission control and request validation. The database points at
a closed port (see ``make_settings``), so startup recovery never completes and the
"not accepting yet" path is exercised for real.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from nexus_api.main import create_app
from tests.conftest import ClientFactory, SettingsFactory
from tests.fakes import HashEmbedder, make_encrypted_pdf, make_pdf

UPLOAD = "/api/v1/documents"


def _file(data: bytes, name: str = "doc.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, data, "application/pdf")}


def _error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


async def test_declared_body_over_limit_is_rejected_before_reading(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(max_upload_bytes=4096), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(UPLOAD, files=_file(b"%PDF-" + b"x" * 200_000))

    assert response.status_code == 413
    assert _error_code(response) == "request_too_large"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_streamed_body_over_limit_is_rejected(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    """No Content-Length (chunked upload): bytes are counted as they arrive."""

    async def body() -> AsyncIterator[bytes]:
        yield (
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
            b"Content-Type: application/pdf\r\n\r\n%PDF-1.7\n"
        )
        for _ in range(100):  # ~400 KiB, never announced up front
            yield b"x" * 4096

    app = create_app(make_settings(max_upload_bytes=4096), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(
            UPLOAD,
            content=body(),
            headers={"Content-Type": "multipart/form-data; boundary=boundary"},
        )

    assert response.status_code == 413
    assert _error_code(response) == "request_too_large"


async def test_file_over_limit_within_body_allowance_is_rejected(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    """The body allowance includes multipart overhead; the file itself is checked exactly."""
    app = create_app(make_settings(max_upload_bytes=4096), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(UPLOAD, files=_file(b"%PDF-" + b"x" * 5000))

    assert response.status_code == 413
    assert _error_code(response) == "file_too_large"


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"", 422, "empty_file"),
        (b"GIF89a not a pdf", 415, "unsupported_media_type"),
        (b"%PDF-1.7 broken", 422, "pdf_invalid"),
        (make_encrypted_pdf(), 422, "pdf_encrypted"),
        (make_pdf([["page"]] * 5), 422, "pdf_too_many_pages"),
    ],
    ids=["empty", "not-pdf", "malformed", "encrypted", "too-many-pages"],
)
async def test_invalid_files_are_rejected_with_specific_errors(
    make_settings: SettingsFactory,
    client_for: ClientFactory,
    data: bytes,
    status: int,
    code: str,
) -> None:
    app = create_app(make_settings(max_pdf_pages=3), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(UPLOAD, files=_file(data))

    assert response.status_code == status
    assert _error_code(response) == code


async def test_content_type_from_client_is_not_trusted(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(
            UPLOAD, files={"file": ("evil.pdf", b"MZ\x90\x00 executable", "application/pdf")}
        )

    assert response.status_code == 415


async def test_missing_file_field_is_a_validation_error(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(UPLOAD, data={"other": "x"})

    assert response.status_code == 422
    assert _error_code(response) == "validation_error"


async def test_valid_upload_is_refused_until_startup_recovery_has_run(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    """With the database down, recovery cannot run, so nothing may be accepted."""
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post(UPLOAD, files=_file(make_pdf([["hello"]])))

    assert response.status_code == 503
    assert _error_code(response) == "ingestion_not_ready"
    assert response.headers["retry-after"] == "5"


async def test_invalid_document_id_is_a_validation_error(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.get(f"{UPLOAD}/not-a-uuid")

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"query": "   "}, "validation_error"),
        ({"query": "x" * 1001}, "validation_error"),
        ({"query": "ok", "top_k": 0}, "validation_error"),
        ({"query": "ok", "top_k": 21}, "top_k_too_large"),
        ({"query": "ok", "document_ids": ["nope"]}, "validation_error"),
    ],
)
async def test_search_request_validation(
    make_settings: SettingsFactory,
    client_for: ClientFactory,
    body: dict[str, object],
    code: str,
) -> None:
    app = create_app(make_settings(), embedder=HashEmbedder())
    async with client_for(app) as client:
        response = await client.post("/api/v1/search", json=body)

    assert response.status_code == 422
    assert _error_code(response) == code


async def test_search_returns_503_when_model_is_unavailable(
    make_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    # No injected embedder and a model cache that does not exist.
    app = create_app(make_settings())
    async with client_for(app) as client:
        response = await client.post("/api/v1/search", json={"query": "anything"})
        readiness = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert _error_code(response) == "embedding_model_unavailable"
    assert readiness.json()["checks"]["embedding_model"] == {
        "status": "down",
        "latency_ms": None,
        "error": "unavailable",
    }
