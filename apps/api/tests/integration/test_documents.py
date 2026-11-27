"""Document ingestion, deduplication, failure handling and search on real PostgreSQL.

The deterministic ``HashEmbedder`` stands in for the neural model, so these tests
verify persistence, state transitions, concurrency and SQL behaviour, not
semantic quality (that is ``tests/model``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import asyncpg
import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexus_api.core.config import Settings
from nexus_api.db.models import DocumentStatus
from nexus_api.documents import ingestion as ingestion_module
from nexus_api.embeddings.base import Embedder
from nexus_api.main import create_app
from tests.conftest import ClientFactory, wait_until, wait_until_accepting
from tests.fakes import BlockingEmbedder, FailingEmbedder, HashEmbedder, make_pdf

pytestmark = pytest.mark.integration

UPLOAD = "/api/v1/documents"
TERMINAL = {DocumentStatus.READY.value, DocumentStatus.FAILED.value}

AppClient = Callable[..., AbstractAsyncContextManager[tuple[FastAPI, httpx.AsyncClient]]]


async def _execute(settings: Settings, query: str, *args: object) -> Any:
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        server_settings={"application_name": "nexus-test-observer"},
    )
    try:
        return await connection.fetchval(query, *args)
    finally:
        await connection.close()


@pytest.fixture(autouse=True)
async def _clean_documents(migrated_settings: Settings) -> None:
    await _execute(migrated_settings, "TRUNCATE documents CASCADE")


@pytest.fixture
def app_client(migrated_settings: Settings, client_for: ClientFactory) -> AppClient:
    """``async with app_client(embedder=..., **settings) as (app, client)``."""

    @asynccontextmanager
    async def factory(
        embedder: Embedder | None = None, **overrides: Any
    ) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
        settings = migrated_settings.model_copy(update=overrides)
        app = create_app(settings, embedder=embedder or HashEmbedder())
        async with client_for(app) as client:
            await wait_until_accepting(app)
            yield app, client

    return factory


def _pdf(*pages: str) -> bytes:
    """A unique PDF per call (a random marker defeats deduplication across tests)."""
    marker = f"marker{uuid.uuid4().hex[:8]}"
    return make_pdf([[line for line in page.split("\n")] for page in pages] + [[marker]])


async def _upload(client: httpx.AsyncClient, data: bytes, name: str = "doc.pdf") -> httpx.Response:
    return await client.post(UPLOAD, files={"file": (name, data, "application/pdf")})


async def _wait_terminal(client: httpx.AsyncClient, document_id: str) -> dict[str, Any]:
    async with asyncio.timeout(15):
        while True:
            body: dict[str, Any] = (await client.get(f"{UPLOAD}/{document_id}")).json()
            if body["status"] in TERMINAL:
                return body
            await asyncio.sleep(0.02)


# --- Happy path, citations, listing --------------------------------------------------


async def test_upload_is_accepted_ingested_and_searchable(app_client: AppClient) -> None:
    async with app_client() as (_, client):
        response = await _upload(
            client,
            _pdf(
                "Photosynthesis converts sunlight into chemical energy in plants.",
                "Glaciers carve valleys over thousands of years.",
                "Volcanic eruptions release ash and lava.",
            ),
            name="../../science notes.pdf",
        )
        assert response.status_code == 202
        accepted = response.json()
        assert accepted["status"] == "pending"
        assert accepted["filename"] == "science notes.pdf"
        assert response.headers["location"] == f"/api/v1/documents/{accepted['id']}"

        document = await _wait_terminal(client, accepted["id"])
        assert document["status"] == "ready"
        assert document["page_count"] == 4
        assert document["chunk_count"] >= 1
        assert document["embedding_model"] == HashEmbedder.model_id
        assert document["error_code"] is None

        search = await client.post("/api/v1/search", json={"query": "glaciers valleys"})

    assert search.status_code == 200
    body = search.json()
    assert body["embedding_model"] == HashEmbedder.model_id
    top = body["results"][0]
    assert top["document_id"] == accepted["id"]
    assert top["filename"] == "science notes.pdf"
    assert top["page_start"] <= 2 <= top["page_end"], "citation points at the right page"
    assert "Glaciers" in top["text"]


async def test_page_citations_are_exact_for_page_sized_chunks(app_client: AppClient) -> None:
    pages = [f"Topic{n} " + " ".join([f"word{n}"] * 30) for n in range(1, 6)]
    # chunk_tokens close to one page of text: most chunks stay on one page.
    async with app_client(chunk_tokens=40, chunk_overlap_tokens=0) as (_, client):
        accepted = (await _upload(client, _pdf(*pages))).json()
        await _wait_terminal(client, accepted["id"])
        for n in range(1, 6):
            hits = (
                await client.post("/api/v1/search", json={"query": f"word{n} topic{n}", "top_k": 1})
            ).json()["results"]
            assert hits[0]["page_start"] == n, (n, hits[0])


async def test_listing_is_newest_first_with_keyset_pagination(app_client: AppClient) -> None:
    async with app_client() as (_, client):
        ids = []
        for n in range(3):
            ids.append((await _upload(client, _pdf(f"document number {n}"))).json()["id"])
            await asyncio.sleep(0.01)  # distinct created_at values
        first = (await client.get(UPLOAD, params={"limit": 2})).json()
        second = (
            await client.get(UPLOAD, params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        invalid = await client.get(UPLOAD, params={"cursor": "%%%not-a-cursor"})

    assert [d["id"] for d in first["items"]] == ids[::-1][:2]
    assert [d["id"] for d in second["items"]] == ids[::-1][2:]
    assert second["next_cursor"] is None
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_cursor"


# --- Deduplication and retries ---------------------------------------------------------


async def test_duplicate_upload_of_ready_document_is_idempotent(app_client: AppClient) -> None:
    data = _pdf("Identical bytes are stored once.")
    embedder = HashEmbedder()
    async with app_client(embedder=embedder) as (_, client):
        first = (await _upload(client, data, "a.pdf")).json()
        await _wait_terminal(client, first["id"])
        calls_after_first = embedder.passage_calls
        duplicate = await _upload(client, data, "renamed.pdf")

    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first["id"]
    assert duplicate.json()["status"] == "ready"
    assert duplicate.json()["filename"] == "a.pdf", "a duplicate does not rename"
    assert embedder.passage_calls == calls_after_first, "no re-embedding"


async def test_concurrent_duplicate_uploads_create_one_document(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    data = _pdf("Five clients upload the same file at the same moment.")
    async with app_client() as (_, client):
        responses = await asyncio.gather(*(_upload(client, data) for _ in range(5)))
        await _wait_terminal(client, responses[0].json()["id"])

    assert sorted(r.status_code for r in responses).count(202) == 1
    assert {r.json()["id"] for r in responses} == {responses[0].json()["id"]}
    assert await _execute(migrated_settings, "SELECT count(*) FROM documents") == 1


async def test_scanned_pdf_fails_with_no_extractable_text_and_retries_predictably(
    app_client: AppClient,
) -> None:
    data = make_pdf([[], []])  # pages without any text layer, like a scan
    async with app_client() as (_, client):
        first = await _upload(client, data)
        failed = await _wait_terminal(client, first.json()["id"])
        retry = await _upload(client, data)
        failed_again = await _wait_terminal(client, retry.json()["id"])

    assert first.status_code == 202
    assert failed["status"] == "failed"
    assert failed["error_code"] == "no_extractable_text"
    assert failed["chunk_count"] is None
    assert retry.status_code == 202, "a failed document is re-queued on re-upload"
    assert retry.json()["id"] == first.json()["id"]
    assert failed_again["attempts"] == 2


async def test_embedding_failure_writes_nothing_and_retry_succeeds(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    data = _pdf("Inference fails the first time.")
    async with app_client(embedder=FailingEmbedder()) as (_, client):
        accepted = (await _upload(client, data)).json()
        failed = await _wait_terminal(client, accepted["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "embedding_failed"
    assert await _execute(migrated_settings, "SELECT count(*) FROM chunks") == 0

    async with app_client(embedder=HashEmbedder()) as (_, client):
        retry = await _upload(client, data)
        ready = await _wait_terminal(client, accepted["id"])
    assert retry.status_code == 202
    assert ready["status"] == "ready"
    assert ready["attempts"] == 2
    assert ready["error_code"] is None


async def test_extraction_failure_is_recorded(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_page(*_: object) -> str:
        raise KeyError("/Contents")  # what a malformed page tree looks like to pypdf

    monkeypatch.setattr(ingestion_module, "extract_page_text", broken_page)
    async with app_client() as (_, client):
        accepted = (await _upload(client, _pdf("parse me"))).json()
        failed = await _wait_terminal(client, accepted["id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "extraction_failed"


# --- Interruption, recovery and admission control ----------------------------------


async def test_graceful_shutdown_marks_in_flight_document_interrupted(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    embedder = BlockingEmbedder()
    async with app_client(embedder=embedder) as (_, client):
        accepted = (await _upload(client, _pdf("never finishes"))).json()
        await asyncio.to_thread(embedder.entered.wait, 10)
        # Leaving the context runs the lifespan shutdown while embedding is blocked.
    embedder.release.set()

    status = await _execute(
        migrated_settings,
        "SELECT status || '/' || error_code FROM documents WHERE id = $1",
        uuid.UUID(accepted["id"]),
    )
    assert status == "failed/interrupted"


async def test_startup_recovers_documents_orphaned_by_a_crash(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    for status in ("pending", "processing"):
        await _execute(
            migrated_settings,
            "INSERT INTO documents (id, filename, sha256, size_bytes, page_count, status, attempts)"
            " VALUES ($1, 'orphan.pdf', $2, 10, 1, $3, 1)",
            uuid.uuid4(),
            uuid.uuid4().hex * 2,
            status,
        )

    async with app_client() as (_, client):
        listing = (await client.get(UPLOAD)).json()["items"]
        fresh = await _upload(client, _pdf("uploaded after recovery"))
        ready = await _wait_terminal(client, fresh.json()["id"])

    orphans = [d for d in listing if d["filename"] == "orphan.pdf"]
    assert len(orphans) == 2
    assert {(d["status"], d["error_code"]) for d in orphans} == {("failed", "interrupted")}
    assert ready["status"] == "ready", "new uploads are unaffected by recovery"


async def test_full_queue_rejects_with_retry_after(app_client: AppClient) -> None:
    embedder = BlockingEmbedder()
    async with app_client(embedder=embedder, ingest_queue_capacity=1) as (app, client):
        first = await _upload(client, _pdf("occupies the only slot"))
        await asyncio.to_thread(embedder.entered.wait, 10)
        rejected = await _upload(client, _pdf("no room"))
        embedder.release.set()
        await _wait_terminal(client, first.json()["id"])
        await wait_until(lambda: app.state.ingestion_runner.outstanding == 0)
        accepted_later = await _upload(client, _pdf("room again"))

    assert first.status_code == 202
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "ingestion_queue_full"
    assert rejected.headers["retry-after"] == "10"
    assert accepted_later.status_code == 202


async def test_no_database_connection_is_held_while_embedding(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    embedder = BlockingEmbedder()
    async with app_client(embedder=embedder) as (app, client):
        await _upload(client, _pdf("slow inference"))
        await asyncio.to_thread(embedder.entered.wait, 10)
        checked_out = app.state.engine.pool.checkedout()
        idle_in_transaction = await _execute(
            migrated_settings,
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'nexus-api'"
            " AND state LIKE 'idle in transaction%'",
        )
        embedder.release.set()

    assert checked_out == 0
    assert idle_in_transaction == 0


# --- Deletion ------------------------------------------------------------------------


async def test_delete_cascades_and_refuses_active_documents(
    app_client: AppClient, migrated_settings: Settings
) -> None:
    embedder = BlockingEmbedder()
    async with app_client(embedder=embedder) as (_, client):
        busy = (await _upload(client, _pdf("busy"))).json()
        await asyncio.to_thread(embedder.entered.wait, 10)
        conflict = await client.delete(f"{UPLOAD}/{busy['id']}")
        embedder.release.set()
        await _wait_terminal(client, busy["id"])
        assert await _execute(migrated_settings, "SELECT count(*) FROM chunks") > 0

        deleted = await client.delete(f"{UPLOAD}/{busy['id']}")
        missing = await client.delete(f"{UPLOAD}/{busy['id']}")
        gone = await client.get(f"{UPLOAD}/{busy['id']}")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "document_busy"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert gone.json()["error"]["code"] == "document_not_found"
    assert await _execute(migrated_settings, "SELECT count(*) FROM chunks") == 0


# --- Search SQL ----------------------------------------------------------------------


async def _insert_document_with_vectors(
    engine: AsyncEngine, vectors: list[np.ndarray[Any, Any]], *, model: str
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO documents (id, filename, sha256, size_bytes, page_count, status,"
                " attempts, embedding_model, chunk_count) VALUES (:id, 'bulk.pdf', :sha, 1, 1,"
                " 'ready', 1, :model, :n)"
            ),
            {"id": document_id, "sha": uuid.uuid4().hex * 2, "model": model, "n": len(vectors)},
        )
        await connection.execute(
            text(
                "INSERT INTO chunks (id, document_id, ordinal, page_start, page_end, text,"
                " token_count, embedding) VALUES (:id, :doc, :ord, 1, 1, 'bulk', 1, :emb)"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "doc": document_id,
                    "ord": i,
                    "emb": "[" + ",".join(f"{x:.6f}" for x in v) + "]",
                }
                for i, v in enumerate(vectors)
            ],
        )
    return document_id


def _unit_vectors(count: int, seed: int) -> list[np.ndarray[Any, Any]]:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(count, 384)).astype(np.float32)
    return list(matrix / np.linalg.norm(matrix, axis=1, keepdims=True))


async def test_search_returns_top_k_when_the_index_scan_is_post_filtered(
    app_client: AppClient, engine: AsyncEngine
) -> None:
    """Most rows belong to another embedding model, so the planner scans the HNSW
    index and filters afterwards. Without ``hnsw.iterative_scan`` only the matches
    among the first ``ef_search`` candidates come back (verified: 5 of 10)."""
    await _insert_document_with_vectors(engine, _unit_vectors(1400, seed=1), model="old/model@1")
    current = await _insert_document_with_vectors(
        engine, _unit_vectors(600, seed=2), model=HashEmbedder.model_id
    )
    async with engine.begin() as connection:
        await connection.execute(text("ANALYZE chunks"))
        await connection.execute(text("ANALYZE documents"))

    async with app_client(search_ef_search=10, search_top_k_max=10) as (_, client):
        response = await client.post("/api/v1/search", json={"query": "anything", "top_k": 10})

    results = response.json()["results"]
    assert len(results) == 10
    assert {r["document_id"] for r in results} == {str(current)}
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_document_filter_restricts_results(
    app_client: AppClient, engine: AsyncEngine
) -> None:
    model = HashEmbedder.model_id
    await _insert_document_with_vectors(engine, _unit_vectors(200, seed=5), model=model)
    target = await _insert_document_with_vectors(engine, _unit_vectors(3, seed=6), model=model)

    async with app_client() as (_, client):
        response = await client.post(
            "/api/v1/search", json={"query": "q", "top_k": 5, "document_ids": [str(target)]}
        )

    results = response.json()["results"]
    assert len(results) == 3, "only the target's three chunks exist"
    assert {r["document_id"] for r in results} == {str(target)}


async def test_search_ignores_chunks_from_other_embedding_models(
    app_client: AppClient, engine: AsyncEngine
) -> None:
    await _insert_document_with_vectors(engine, _unit_vectors(5, seed=3), model="other/model@1")
    async with app_client() as (_, client):
        response = await client.post("/api/v1/search", json={"query": "anything"})

    assert response.json()["results"] == []


async def test_vector_search_uses_the_hnsw_index(engine: AsyncEngine) -> None:
    await _insert_document_with_vectors(engine, _unit_vectors(50, seed=4), model="m")
    probe = "[" + ",".join(["0.05"] * 384) + "]"
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            (
                await connection.execute(
                    text(
                        "EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> CAST(:q AS vector)"
                        " LIMIT 5"
                    ),
                    {"q": probe},
                )
            )
            .scalars()
            .all()
        )

    assert any("ix_chunks_embedding_hnsw" in line for line in plan), plan


async def test_schema_constraints_reject_invalid_rows(migrated_settings: Settings) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _execute(
            migrated_settings,
            "INSERT INTO documents (id, filename, sha256, size_bytes, page_count, status, attempts)"
            " VALUES ($1, 'x', $2, 1, 1, 'exploded', 1)",
            uuid.uuid4(),
            "a" * 64,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await _execute(
            migrated_settings,
            "INSERT INTO documents (id, filename, sha256, size_bytes, page_count, status, attempts)"
            " VALUES ($1, 'x', $2, 1, 0, 'pending', 1)",
            uuid.uuid4(),
            "b" * 64,
        )
