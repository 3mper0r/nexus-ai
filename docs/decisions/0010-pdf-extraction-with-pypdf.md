# ADR 0010: PDF extraction with pypdf instead of PyMuPDF

* Status: Accepted (M1, 2026-11-27). Supersedes the PyMuPDF entry of the M0 technology list.

## Context
The original stack named PyMuPDF. PyMuPDF is dual-licensed **AGPL-3.0 or commercial**.
Using it in a network service obliges the operator to offer the complete corresponding
source of the service under AGPL terms, which complicates any later private or commercial
use of NEXUS AI. The project had no licence decision yet.

## Decision
Use **pypdf 7.x (BSD-3-Clause)**, pure Python, for validation and per-page text
extraction (`extraction_mode="plain"`). OCR is out of scope.

Validation (`documents/pdf.py`): `%PDF-` within the first 1 KiB (the client content type
is never trusted), parse with `strict=False`, try an **empty user password** for
"owner-password-only" PDFs and reject anything else as `pdf_encrypted`, reject zero pages
and more than `MAX_PDF_PAGES`. Any parser exception on untrusted bytes maps to
`pdf_invalid`.

Normalisation: NFKC (folds ligatures and full-width forms), strip NUL/control characters
(PostgreSQL `text` rejects NUL), re-join words hyphenated across a line break only between
two lower-case letters (keeps German compounds like `Datenschutz-Grundverordnung`),
collapse horizontal whitespace, keep paragraph breaks.

## Measured quality (py-pdf sample corpus, CC-BY-SA-4.0, 2026-11-27)

| File | Producer | Result |
|---|---|---|
| GeoTopo (117 pp., German) | pdfLaTeX | Umlauts and ß correct; prose and definitions clean; **math symbols lost or garbled** (`‖x₁−x₂‖` → `kx1 x2k`); figure labels become noise passages; running headers and page numbers inside passages |
| multicolumn (3 pp.) | LaTeX two-column | Column order correct on these pages |
| google-doc-document | Google Docs | Clean |
| 002 trivial | LibreOffice Writer | Clean |
| libreoffice-writer-password | LibreOffice, user password | Rejected `pdf_encrypted` (422) |
| imagemagick-images | images only | A few stray words; real scans yield `no_extractable_text` |

Throughput: the 117-page document was extracted, chunked and embedded in ~24 s on 8 CPU
cores (extraction is a minor share; embedding dominates).

## Extracted-text limits (added in the M1 audit)
pypdf caps each decompressed stream at 75 MB, but a ~90 KiB PDF whose 500 pages share one
compressed text stream extracted to 5,000,000 characters per page (≈ 2.5 GB of text and
≈ 1.5 M chunks for the document). Ingestion therefore enforces `MAX_PAGE_CHARS` (100,000) on
the **raw** extraction of each page (whitespace-only text normalises to nothing and would
otherwise bypass the cap) and `MAX_DOCUMENT_CHARS` (5,000,000) on the running total, failing
with `text_limit_exceeded` as soon as either is exceeded. Measured: the 90 KiB bomb now fails
after 2.8 s. Pages full of drawing operators without text cost ~0.1 s each (bounded).

## Consequences
* + Permissive licence; no native dependency; pure-Python parser cannot corrupt memory.
* + Same API surface would allow swapping in another extractor behind `pdf.py`.
* − Lower layout fidelity than PyMuPDF: no reading-order analysis for complex layouts,
  no table structure, math fonts without ToUnicode maps extract poorly, headers and
  footers are not removed.
* − Slower than PyMuPDF (C library) on large files; acceptable at current limits.
* − AES-encrypted files with an empty user password need the optional `cryptography`
  extra; without it they are rejected as encrypted.
* Rejected: PyMuPDF (licence), pdfminer.six (MIT, better layout analysis but slower and a
  larger API; a candidate if layout quality becomes the bottleneck), OCR (Tesseract):
  scope.
