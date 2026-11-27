"""End-to-end with the real model and real PostgreSQL (markers ``integration`` + ``model``).

Upload a bilingual handbook PDF through the API, wait for ingestion, and check
that English and German queries (including cross-lingual ones) return the right
document and page.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from nexus_api.core.config import Settings
from nexus_api.embeddings.base import Embedder
from nexus_api.main import create_app
from tests.conftest import ClientFactory, wait_until_accepting
from tests.fakes import make_pdf
from tests.model.corpus import PASSAGES

pytestmark = [pytest.mark.integration, pytest.mark.model]

TOPIC_PAGES = ["retention", "vacation", "password", "travel", "fire"]


def _handbook() -> bytes:
    """Page n holds topic n: English paragraph, blank line, German paragraph."""
    pages = []
    for topic in TOPIC_PAGES:
        lines = [*_wrap(PASSAGES[f"{topic}-en"]), "", *_wrap(PASSAGES[f"{topic}-de"])]
        pages.append(lines)
    return make_pdf(pages)


def _wrap(text: str, width: int = 80) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current]


async def _wait_ready(client: httpx.AsyncClient, document_id: str) -> dict[str, Any]:
    async with asyncio.timeout(60):
        while True:
            body: dict[str, Any] = (await client.get(f"/api/v1/documents/{document_id}")).json()
            if body["status"] in {"ready", "failed"}:
                return body
            await asyncio.sleep(0.1)


@pytest.mark.parametrize(
    ("query", "page"),
    [
        ("How long is customer data retained?", 1),
        ("Wie viele Urlaubstage bekomme ich?", 2),
        ("Wie lang muss ein Passwort sein?", 3),
        ("Is a flight allowed for business travel?", 4),
        ("Wo ist der Sammelplatz im Brandfall?", 5),
    ],
)
async def test_bilingual_pdf_search_cites_the_right_page(
    migrated_settings: Settings,
    client_for: ClientFactory,
    e5_embedder: Embedder,
    query: str,
    page: int,
) -> None:
    # Small chunks (~one paragraph): with the default 400 tokens this five-page
    # handbook would be a single chunk citing pages 1-5, and the page assertion
    # below would be vacuous.
    settings = migrated_settings.model_copy(update={"chunk_tokens": 48, "chunk_overlap_tokens": 0})
    app = create_app(settings, embedder=e5_embedder)
    async with client_for(app) as client:
        await wait_until_accepting(app)
        upload = await client.post(
            "/api/v1/documents",
            files={"file": ("Mitarbeiterhandbuch.pdf", _handbook(), "application/pdf")},
        )
        assert upload.status_code in {200, 202}  # first parametrisation ingests, others dedupe
        document = await _wait_ready(client, upload.json()["id"])
        response = await client.post("/api/v1/search", json={"query": query, "top_k": 3})

    assert document["status"] == "ready", document
    assert document["embedding_model"] == e5_embedder.model_id
    top = response.json()["results"][0]
    assert top["filename"] == "Mitarbeiterhandbuch.pdf"
    assert document["chunk_count"] >= len(TOPIC_PAGES)
    assert (top["page_start"], top["page_end"]) == (page, page), (query, top)
