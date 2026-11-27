"""Deterministic test doubles: an embedder and a minimal PDF generator.

``HashEmbedder`` satisfies the ``Embedder`` protocol without PyTorch: each word
is hashed into one of 384 dimensions (feature hashing), so texts sharing words
are close in cosine space. That is enough to test ranking, filtering and
persistence deterministically; semantic and multilingual quality is tested
against the real model separately (``-m model``).
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
from collections.abc import Sequence

import numpy as np
from pypdf import PdfReader, PdfWriter

from nexus_api.embeddings.base import EMBEDDING_DIMENSION, FloatMatrix, TokenSpan

_TOKEN = re.compile(r"\w+|[^\w\s]")


class HashEmbedder:
    model_id = "test/hash-embedder@0"
    dimension = EMBEDDING_DIMENSION

    def __init__(self) -> None:
        self.passage_calls = 0
        self.passages_embedded = 0

    def token_spans(self, text: str) -> list[TokenSpan]:
        return [match.span() for match in _TOKEN.finditer(text)]

    def embed_passages(self, texts: Sequence[str]) -> FloatMatrix:
        self.passage_calls += 1
        self.passages_embedded += len(texts)
        return np.stack([self._vector(text) for text in texts])

    def embed_query(self, text: str) -> FloatMatrix:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> FloatMatrix:
        vector = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % EMBEDDING_DIMENSION
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        result: FloatMatrix = vector / norm
        return result


class FailingEmbedder(HashEmbedder):
    """Fails while embedding passages (simulates an inference error)."""

    def embed_passages(self, texts: Sequence[str]) -> FloatMatrix:
        raise RuntimeError("simulated inference failure")


class BlockingEmbedder(HashEmbedder):
    """Blocks inside ``embed_passages`` until released (simulates slow inference)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed_passages(self, texts: Sequence[str]) -> FloatMatrix:
        self.entered.set()
        self.release.wait(timeout=10)
        return super().embed_passages(texts)


# --- PDF generation ----------------------------------------------------------------


def _pdf_string(text: str) -> bytes:
    # WinAnsiEncoding covers Latin-1, including German umlauts and ß.
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("cp1252") + b")"


def make_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Build a valid PDF; each page is a list of text lines (empty list = no text)."""
    objects: list[bytes] = []
    page_count = len(pages)
    font_id = 3 + 2 * page_count
    page_ids = [3 + 2 * i for i in range(page_count)]

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
    objects.append(b"<< /Type /Pages /Kids [" + kids + f"] /Count {page_count} >>".encode())
    for index, lines in enumerate(pages):
        content_id = page_ids[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        ops = [b"BT /F1 11 Tf 14 TL 56 760 Td"]
        ops.extend(_pdf_string(line) + b" Tj T*" for line in lines)
        ops.append(b"ET")
        stream = b"\n".join(ops)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def make_encrypted_pdf(user_password: str = "secret") -> bytes:  # noqa: S107 - test fixture
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([["Confidential"]]))))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_owner_only_encrypted_pdf() -> bytes:
    """Encrypted with an empty user password: opens without a password."""
    return make_encrypted_pdf(user_password="")


def paragraph(words: int, seed: str = "lorem") -> str:
    """Deterministic filler text of ``words`` words with sentence punctuation."""
    vocabulary = [f"{seed}{i % 37}" for i in range(words)]
    sentences = [" ".join(vocabulary[i : i + 12]) + "." for i in range(0, words, 12)]
    return " ".join(sentences)
