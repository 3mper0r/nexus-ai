"""PDF validation and per-page text extraction with pypdf (BSD-3-Clause).

All functions here are synchronous and CPU-bound: callers run them through
``asyncio.to_thread`` so the event loop keeps serving requests.

Threat model: uploads are untrusted. We only parse structure and extract text. We
never render pages, run embedded JavaScript, follow embedded files or fetch
external resources. pypdf enforces its own decompression and xref limits; on top
of that the upload size and page count are capped. pypdf is pure Python, so a
pathological file costs CPU time but cannot corrupt memory. The residual risk (a
thread that cannot be interrupted while parsing a hostile file) is documented in
ADR 0012.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

import structlog
from pypdf import PasswordType, PdfReader
from pypdf.errors import DependencyError

from nexus_api.documents.errors import (
    EmptyFileError,
    PdfEncryptedError,
    PdfInvalidError,
    PdfTooManyPagesError,
    UnsupportedMediaTypeError,
)

logger = structlog.stdlib.get_logger(__name__)

PDF_MAGIC = b"%PDF-"
# The PDF specification lets readers accept the header anywhere in the first 1 KiB.
PDF_HEADER_WINDOW = 1024

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHENATED_BREAK = re.compile(r"(?<=[a-zäöüß])-\n(?=[a-zäöüß])")
_HORIZONTAL_WS = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class OpenedPdf:
    reader: PdfReader
    page_count: int


def open_pdf(data: bytes, *, max_pages: int) -> OpenedPdf:
    """Validate an uploaded file and open it. Raises a 4xx ``AppError`` on rejection."""
    if not data:
        raise EmptyFileError("The uploaded file is empty.")
    if PDF_MAGIC not in data[:PDF_HEADER_WINDOW]:
        # The client-declared content type is never trusted; the bytes decide.
        raise UnsupportedMediaTypeError("Only PDF files are supported.")

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            _decrypt_with_empty_password(reader)
        page_count = len(reader.pages)
    except (PdfEncryptedError, PdfInvalidError):
        raise
    except Exception as exc:
        # Malformed input can surface as almost any exception type inside a parser
        # (KeyError, ValueError, PdfReadError, RecursionError...). For untrusted
        # bytes they all mean the same thing to the caller.
        logger.info("pdf_rejected", error_type=type(exc).__name__)
        raise PdfInvalidError("The file is not a readable PDF.") from exc

    if page_count == 0:
        raise PdfInvalidError("The PDF has no pages.")
    if page_count > max_pages:
        raise PdfTooManyPagesError(f"The PDF has {page_count} pages; the limit is {max_pages}.")
    return OpenedPdf(reader=reader, page_count=page_count)


def _decrypt_with_empty_password(reader: PdfReader) -> None:
    """Many PDFs are 'encrypted' only with an owner password (print/copy restrictions)
    and open with an empty user password. Anything else is rejected."""
    try:
        result = reader.decrypt("")
    except (DependencyError, NotImplementedError) as exc:
        # AES-encrypted files need the optional `cryptography` dependency.
        raise PdfEncryptedError("Encrypted PDFs are not supported.") from exc
    if result == PasswordType.NOT_DECRYPTED:
        raise PdfEncryptedError("Password-protected PDFs are not supported.")


class PageTextLimitError(Exception):
    """A page extracted to more raw text than allowed (likely a text-expansion bomb)."""


def extract_page_text(pdf: OpenedPdf, index: int, *, max_chars: int | None = None) -> str:
    """Extract and normalise the text of one page (0-based index).

    ``max_chars`` is checked on the *raw* extraction: normalisation can collapse
    millions of whitespace characters to nothing, which would hide the cost of a
    hostile page from a limit applied afterwards.
    """
    raw = pdf.reader.pages[index].extract_text()
    if max_chars is not None and len(raw) > max_chars:
        raise PageTextLimitError(f"page {index + 1}: {len(raw)} characters")
    return normalize_text(raw)


def normalize_text(text: str) -> str:
    """Make extracted text safe to store and consistent to embed.

    * NFKC folds ligatures (``ﬁ`` → ``fi``) and full-width forms, which PDF fonts
      produce often and which would otherwise split identical words into different
      tokens.
    * NUL and other control characters are removed (PostgreSQL ``text`` rejects
      NUL outright).
    * Words hyphenated across a line break are re-joined only between two
      lower-case letters (``Verarbei-\\ntung`` → ``Verarbeitung``), which keeps
      genuine hyphenated compounds such as ``Datenschutz-\\nGrundverordnung``.
    * Horizontal whitespace is collapsed; paragraph breaks are preserved because
      the chunker prefers to cut there.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    text = _HYPHENATED_BREAK.sub("", text)
    text = _HORIZONTAL_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
