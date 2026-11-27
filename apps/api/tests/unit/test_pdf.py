"""PDF validation, extraction and text normalisation on generated PDFs."""

from __future__ import annotations

import pytest

from nexus_api.core.errors import AppError
from nexus_api.documents.pdf import (
    PageTextLimitError,
    extract_page_text,
    normalize_text,
    open_pdf,
)
from nexus_api.documents.service import sanitize_filename
from tests.fakes import make_encrypted_pdf, make_owner_only_encrypted_pdf, make_pdf


def test_extracts_text_per_page_including_german_characters() -> None:
    pdf = open_pdf(
        make_pdf([["Datenschutz und Größe", "Straße ß äöü ÄÖÜ"], [], ["Third page"]]),
        max_pages=10,
    )

    assert pdf.page_count == 3
    texts = [extract_page_text(pdf, i) for i in range(3)]
    assert texts == ["Datenschutz und Größe\nStraße ß äöü ÄÖÜ", "", "Third page"]


def test_header_may_follow_leading_bytes_within_first_kib() -> None:
    assert open_pdf(b"\n" * 100 + make_pdf([["ok"]]), max_pages=1).page_count == 1


def test_owner_password_only_pdf_opens() -> None:
    pdf = open_pdf(make_owner_only_encrypted_pdf(), max_pages=5)
    assert extract_page_text(pdf, 0) == "Confidential"


@pytest.mark.parametrize(
    ("data", "code", "status"),
    [
        (b"", "empty_file", 422),
        (b"PK\x03\x04 a zip file", "unsupported_media_type", 415),
        (b"x" * 2000 + b"%PDF-1.7", "unsupported_media_type", 415),
        (b"%PDF-1.7\nthis is not a pdf body", "pdf_invalid", 422),
        (make_pdf([["truncated"]])[:120], "pdf_invalid", 422),
        (make_encrypted_pdf(), "pdf_encrypted", 422),
        (make_pdf([]), "pdf_invalid", 422),
    ],
    ids=["empty", "zip", "header-too-late", "garbage", "truncated", "encrypted", "no-pages"],
)
def test_rejects_invalid_uploads(data: bytes, code: str, status: int) -> None:
    with pytest.raises(AppError) as excinfo:
        open_pdf(data, max_pages=10)
    assert excinfo.value.code == code
    assert excinfo.value.status_code == status


def test_rejects_too_many_pages() -> None:
    with pytest.raises(AppError) as excinfo:
        open_pdf(make_pdf([["p"]] * 4), max_pages=3)
    assert excinfo.value.code == "pdf_too_many_pages"
    assert "4 pages" in excinfo.value.message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ﬁnance ﬂow", "finance flow"),  # ligatures folded (NFKC)
        ("a\x00b\x07c", "abc"),  # NUL / control characters removed
        ("Verarbei-\ntung", "Verarbeitung"),  # hyphenation across lines re-joined
        ("Datenschutz-\nGrundverordnung", "Datenschutz-\nGrundverordnung"),  # compound kept
        ("too    many\t\tspaces\u00a0here", "too many spaces here"),
        ("  line one  \r\n\r\n\r\n\r\n line two ", "line one\n\nline two"),
        ("\uff21\uff42\uff43\uff11\uff12\uff13", "Abc123"),  # full-width forms
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "document.pdf"),
        ("", "document.pdf"),
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\Q3 Bericht.pdf", "Q3 Bericht.pdf"),
        ('bad<>:"|?*name.pdf', "badname.pdf"),
        ("new\nline\x00.pdf", "newline.pdf"),
        ("  ...  ", "document.pdf"),
        ("Übersicht.pdf", "Übersicht.pdf"),
        # Audit: bidi override would display "invoice\u202efdp.exe" as "invoiceexe.pdf".
        ("invoice\u202efdp.exe", "invoicefdp.exe"),
        ("zero\u200bwidth\u2028sep.pdf", "zerowidthsep.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_caps_length_and_keeps_extension() -> None:
    name = sanitize_filename("a" * 400 + ".pdf")
    assert len(name) == 255
    assert name.endswith(".pdf")


def test_page_text_limit_applies_to_raw_text_before_normalisation() -> None:
    """Audit: whitespace normalises to nothing, so a post-normalisation cap is bypassable."""
    pdf = open_pdf(make_pdf([[" " * 3000 + "x"]]), max_pages=1)

    assert extract_page_text(pdf, 0) == "x"
    with pytest.raises(PageTextLimitError):
        extract_page_text(pdf, 0, max_chars=1000)
