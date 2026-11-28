"""Context assembly: retrieved chunks -> budgeted, grouped ``search_result`` sources.

Pure functions. Chunks are taken in relevance order until the character budget is
spent, then grouped per document (documents ordered by their best hit, passages
by position in the document). Each passage keeps its chunk id and page, which is
how a citation's (result index, block index) maps back to an exact page.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from nexus_api.documents.repository import SearchHit
from nexus_api.llm.generator import SourceDocument

SOURCE_PREFIX = "nexus://documents/"


@dataclass(frozen=True, slots=True)
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    ordinal: int
    page: int
    text: str
    score: float


@dataclass(frozen=True, slots=True)
class ContextDocument:
    document_id: uuid.UUID
    filename: str
    passages: Sequence[Passage]

    def as_source(self) -> SourceDocument:
        return SourceDocument(
            source=f"{SOURCE_PREFIX}{self.document_id}",
            title=self.filename,
            passages=[p.text for p in self.passages],
        )


def assemble_context(hits: Sequence[SearchHit], *, char_budget: int) -> list[ContextDocument]:
    """Select passages within ``char_budget`` and group them per document."""
    selected: list[Passage] = []
    used = 0
    for hit in hits:  # already ordered by relevance
        remaining = char_budget - used
        if len(hit.text) <= remaining:
            text = hit.text
        elif not selected:
            text = hit.text[:remaining]  # never send nothing because one passage is long
        else:
            continue  # a later, shorter passage may still fit
        selected.append(
            Passage(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                filename=hit.filename,
                ordinal=hit.ordinal,
                page=hit.page_start,
                text=text,
                score=hit.score,
            )
        )
        used += len(text)

    order: list[uuid.UUID] = []
    grouped: dict[uuid.UUID, list[Passage]] = {}
    for passage in selected:
        if passage.document_id not in grouped:
            order.append(passage.document_id)
            grouped[passage.document_id] = []
        grouped[passage.document_id].append(passage)
    return [
        ContextDocument(
            document_id=doc_id,
            filename=grouped[doc_id][0].filename,
            passages=sorted(grouped[doc_id], key=lambda p: p.ordinal),
        )
        for doc_id in order
    ]


def context_chars(documents: Sequence[ContextDocument]) -> int:
    return sum(len(p.text) for d in documents for p in d.passages)
