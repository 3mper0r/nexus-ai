"""Regression tests for defects found in the M1 engineering audit (2026-11-27)."""

from __future__ import annotations

import asyncio
import threading
import uuid
import zlib
from typing import Any

import asyncpg
import httpx
import pytest
from asgi_lifespan import LifespanManager

from nexus_api import main as main_module
from nexus_api.core.config import Settings
from nexus_api.embeddings.base import Embedder
from nexus_api.main import create_app
from tests.conftest import ClientFactory, wait_until_accepting
from tests.fakes import HashEmbedder, make_pdf

pytestmark = pytest.mark.integration

UPLOAD = "/api/v1/documents"


async def _fetch(settings: Settings, query: str, *args: object) -> Any:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
    )
    try:
        return await connection.fetchval(query, *args)
    finally:
        await connection.close()


@pytest.fixture(autouse=True)
async def _clean_documents(migrated_settings: Settings) -> None:
    await _fetch(migrated_settings, "TRUNCATE documents CASCADE")


async def _upload(client: httpx.AsyncClient, data: bytes) -> httpx.Response:
    return await client.post(UPLOAD, files={"file": ("doc.pdf", data, "application/pdf")})


async def _wait_terminal(client: httpx.AsyncClient, document_id: str) -> dict[str, Any]:
    async with asyncio.timeout(20):
        while True:
            body: dict[str, Any] = (await client.get(f"{UPLOAD}/{document_id}")).json()
            if body["status"] in {"ready", "failed"}:
                return body
            await asyncio.sleep(0.02)


def _text_bomb(pages: int, chars_per_page: int, unit: str = "lorem ipsum dolor sit amet ") -> bytes:
    """Tiny file, huge text: every page shares one highly compressed content stream."""
    words = (unit * (chars_per_page // len(unit) + 1))[:chars_per_page]
    stream = zlib.compress(b"BT /F1 10 Tf 0 0 Td (" + words.encode() + b") Tj ET", 9)
    content_id, font_id = 3 + pages, 4 + pages
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{3 + i} 0 R".encode() for i in range(pages))
        + f"] /Count {pages} >>".encode(),
        *[
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font"
            f" << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
            for _ in range(pages)
        ],
        f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


async def test_text_expansion_bomb_is_stopped_early(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    """A ~90 KiB PDF expanding to 5,000,000 characters per page must not be ingested."""
    embedder = HashEmbedder()
    app = create_app(migrated_settings, embedder=embedder)
    async with client_for(app) as client:
        await wait_until_accepting(app)
        response = await _upload(client, _text_bomb(pages=50, chars_per_page=5_000_000))
        assert response.status_code == 202
        document = await _wait_terminal(client, response.json()["id"])

    assert document["status"] == "failed"
    assert document["error_code"] == "text_limit_exceeded"
    assert embedder.passages_embedded == 0, "nothing may be embedded"


async def test_whitespace_expansion_bomb_is_stopped_early(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    """Millions of spaces normalise to an empty page: the limit must apply to raw text."""
    app = create_app(migrated_settings, embedder=HashEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)
        bomb = _text_bomb(pages=50, chars_per_page=5_000_000, unit=" ")
        response = await _upload(client, bomb)
        document = await _wait_terminal(client, response.json()["id"])

    assert document["error_code"] == "text_limit_exceeded"


async def test_document_text_limit_applies_across_pages(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    settings = migrated_settings.model_copy(
        update={"max_page_chars": 5_000, "max_document_chars": 10_000}
    )
    app = create_app(settings, embedder=HashEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)
        response = await _upload(client, _text_bomb(pages=5, chars_per_page=4_000))
        document = await _wait_terminal(client, response.json()["id"])

    assert document["error_code"] == "text_limit_exceeded"


async def test_upload_is_refused_while_the_model_is_loading(
    migrated_settings: Settings, client_for: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Previously accepted with 202 and then failed as internal_error."""
    release = threading.Event()

    def slow_loader(_settings: Settings) -> Embedder:
        release.wait(timeout=10)
        return HashEmbedder()

    monkeypatch.setattr(main_module, "_load_default_embedder", slow_loader)
    app = create_app(migrated_settings)
    try:
        # LifespanManager directly: client_for would wait for the model to settle.
        async with LifespanManager(app, startup_timeout=10, shutdown_timeout=20):
            await wait_until_accepting(app)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                response = await _upload(client, make_pdf([["loading"]]))
    finally:
        release.set()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "embedding_model_unavailable"
    assert response.headers["retry-after"] == "5"
    assert await _fetch(migrated_settings, "SELECT count(*) FROM documents") == 0


class OtherModelEmbedder(HashEmbedder):
    model_id = "test/other-model@1"


async def test_reupload_after_model_change_reingests_with_the_active_model(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    """A document embedded by another model is unsearchable; re-upload must re-embed it."""
    data = make_pdf([[f"glacier valley erosion {uuid.uuid4().hex}"]])
    app = create_app(migrated_settings, embedder=HashEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)
        first = (await _upload(client, data)).json()
        await _wait_terminal(client, first["id"])

    app = create_app(migrated_settings, embedder=OtherModelEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)
        retry = await _upload(client, data)
        document = await _wait_terminal(client, first["id"])
        search = (await client.post("/api/v1/search", json={"query": "glacier valley"})).json()

    assert retry.status_code == 202
    assert document["status"] == "ready"
    assert document["embedding_model"] == OtherModelEmbedder.model_id
    assert document["attempts"] == 2
    assert search["results"], "the document is searchable with the active model"
    models = await _fetch(
        migrated_settings,
        "SELECT count(DISTINCT d.embedding_model) FROM chunks c"
        " JOIN documents d ON d.id = c.document_id",
    )
    assert models == 1


# --- Coverage gaps found in the audit ------------------------------------------------


async def test_concurrent_retries_of_a_failed_document_requeue_it_once(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    data = make_pdf([[], []])  # no text layer: fails deterministically
    app = create_app(migrated_settings, embedder=HashEmbedder())
    async with client_for(app) as client:
        await wait_until_accepting(app)
        first = (await _upload(client, data)).json()
        await _wait_terminal(client, first["id"])
        retries = await asyncio.gather(*(_upload(client, data) for _ in range(4)))
        final = await _wait_terminal(client, first["id"])

    assert sorted(r.status_code for r in retries) == [200, 200, 200, 202]
    assert final["attempts"] == 2


async def test_shutdown_marks_queued_and_running_documents_interrupted(
    migrated_settings: Settings, client_for: ClientFactory
) -> None:
    from tests.fakes import BlockingEmbedder

    embedder = BlockingEmbedder()
    settings = migrated_settings.model_copy(update={"ingest_queue_capacity": 3})
    app = create_app(settings, embedder=embedder)
    async with client_for(app) as client:
        await wait_until_accepting(app)
        running = (await _upload(client, make_pdf([["running document"]]))).json()
        await asyncio.to_thread(embedder.entered.wait, 10)
        queued = (await _upload(client, make_pdf([["queued document"]]))).json()
    embedder.release.set()

    for document in (running, queued):
        state = await _fetch(
            migrated_settings,
            "SELECT status || '/' || error_code FROM documents WHERE id = $1",
            uuid.UUID(document["id"]),
        )
        assert state == "failed/interrupted", document["id"]
