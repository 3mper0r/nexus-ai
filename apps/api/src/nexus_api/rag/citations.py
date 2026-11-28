"""Validated, numbered citations (ADR 0017).

Citations come only from the API's structured ``search_result_location`` objects,
never from text the model writes. Each one is checked against what was actually
sent: the result index and the block range must point at passages we provided.
Anything else is dropped and logged. The text shown to users is always our stored
passage text, never ``cited_text`` echoed back by the model.

Repeated citations of the same passages reuse one number, so the answer's markers
and the sources list stay consistent.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from nexus_api.llm.generator import CitationRef
from nexus_api.rag.context import ContextDocument

logger = structlog.stdlib.get_logger(__name__)

# Markers embedded in stored answer Markdown: "[3](#cite-3)". The UI renders them as
# citation chips; they are stripped before an answer is sent back to Claude as history.
MARKER_TEMPLATE = "[{n}](#cite-{n})"
MARKER_PATTERN = re.compile(r"\[(\d{1,3})\]\(#cite-\1\)")


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    number: int
    document_id: uuid.UUID
    filename: str
    page_start: int
    page_end: int
    chunk_ids: tuple[uuid.UUID, ...]
    text: str

    def as_json(self) -> dict[str, object]:
        return {
            "number": self.number,
            "document_id": str(self.document_id),
            "filename": self.filename,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_ids": [str(c) for c in self.chunk_ids],
            "text": self.text,
        }


class CitationResolver:
    def __init__(self, documents: Sequence[ContextDocument]) -> None:
        self._documents = documents
        self._numbers: dict[tuple[int, int, int], ResolvedCitation] = {}
        self.dropped = 0

    @property
    def citations(self) -> list[ResolvedCitation]:
        return sorted(self._numbers.values(), key=lambda c: c.number)

    def resolve(self, ref: CitationRef) -> tuple[ResolvedCitation, bool] | None:
        """Return the citation and whether it is new, or ``None`` if invalid."""
        key = (ref.search_result_index, ref.start_block_index, ref.end_block_index)
        if key in self._numbers:
            return self._numbers[key], False
        if not 0 <= ref.search_result_index < len(self._documents):
            self._drop(ref, "unknown search result")
            return None
        document = self._documents[ref.search_result_index]
        if not 0 <= ref.start_block_index < ref.end_block_index <= len(document.passages):
            self._drop(ref, "block range outside the search result")
            return None
        passages = document.passages[ref.start_block_index : ref.end_block_index]
        citation = ResolvedCitation(
            number=len(self._numbers) + 1,
            document_id=document.document_id,
            filename=document.filename,
            page_start=min(p.page for p in passages),
            page_end=max(p.page for p in passages),
            chunk_ids=tuple(p.chunk_id for p in passages),
            text="\n\n".join(p.text for p in passages),
        )
        self._numbers[key] = citation
        return citation, True

    def _drop(self, ref: CitationRef, reason: str) -> None:
        self.dropped += 1
        logger.warning(
            "llm_invalid_citation_dropped",
            reason=reason,
            search_result_index=ref.search_result_index,
            start=ref.start_block_index,
            end=ref.end_block_index,
        )


def marker(number: int) -> str:
    return MARKER_TEMPLATE.format(n=number)


def strip_markers(text: str) -> str:
    return MARKER_PATTERN.sub("", text)
