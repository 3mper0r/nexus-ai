"""App factory for browser end-to-end checks without an Anthropic API key.

Everything is real (FastAPI, PostgreSQL, the embedding model, retrieval, citations,
SSE, persistence) except the answer generator, which streams a scripted Markdown
answer citing the first retrieved passages, at a token-like pace, so the UI's
streaming, citation chips, sources list, Stop button and Markdown rendering can be
exercised in a browser.

Run: ``uv run --env-file ../../.env uvicorn tests.e2e.demo_app:create_demo_app --factory``
Test infrastructure only; never used by the application.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from nexus_api.core.config import Settings
from nexus_api.llm.generator import (
    CitationRef,
    Completed,
    GenerationEvent,
    GenerationRequest,
    TextDelta,
)
from nexus_api.llm.pricing import TokenUsage
from nexus_api.main import create_app

ANSWER_PARTS = [
    ("## Summary\n\nThe handbook answers this directly. ", None),
    ("Customer data is kept for a limited period.", (0, 0)),
    (
        "\n\n| Topic | Rule |\n|---|---|\n"
        "| Retention | Three years |\n| Deletion | Automatic |\n\n",
        None,
    ),
    ("Steps:\n\n1. Check the retention period.\n2. Delete expired records.\n\n", None),
    (
        "Example query:\n\n```sql\nSELECT * FROM customers\n"
        "WHERE ended < now() - interval '3 years';\n```\n\n",
        None,
    ),
    ("<script>alert('x')</script>Raw HTML from a model is never rendered. ", None),
    ("A second passage supports the deletion rule.", (0, 1)),
]


class DemoGenerator:
    def __init__(self, delay: float = 0.03) -> None:
        self.delay = delay

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        await asyncio.sleep(0.4)  # "thinking"
        for block, (text, cite) in enumerate(ANSWER_PARTS):
            if cite is not None and request.sources:
                result, passage = cite
                passages = len(request.sources[min(result, len(request.sources) - 1)].passages)
                yield CitationRef(
                    block=block,
                    search_result_index=min(result, len(request.sources) - 1),
                    start_block_index=min(passage, passages - 1),
                    end_block_index=min(passage, passages - 1) + 1,
                )
            for word in text.split(" "):
                yield TextDelta(text=word + " ", block=block)
                await asyncio.sleep(self.delay)
        yield Completed(
            stop_reason="end_turn",
            usage=TokenUsage(input_uncached=1800, cache_creation=0, cache_read=740, output=210),
            model="claude-sonnet-5-5",
        )


def create_demo_app() -> FastAPI:
    return create_app(Settings(), answer_generator=DemoGenerator())
