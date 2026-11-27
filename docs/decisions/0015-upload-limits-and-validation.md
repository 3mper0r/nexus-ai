# ADR 0015: Upload path, size limits and validation

* Status: Accepted (M1, 2026-11-27)

## Decision
* **Path:** browser → Next.js **Server Action** → API (consistent with ADR 0007; the browser
  never calls the API). `experimental.serverActions.bodySizeLimit = "21mb"` (20 MiB + multipart
  overhead; the default is 1 MB). Server Actions include Next.js's same-origin CSRF check.
* **API size limit on the raw stream:** Starlette parses multipart bodies completely (spooling
  to disk) before an endpoint runs, so a pure-ASGI `BodySizeLimitMiddleware` enforces
  `MAX_UPLOAD_BYTES + 64 KiB`: a larger `Content-Length` is rejected immediately; otherwise
  bytes are counted as they arrive. FastAPI converts exceptions raised while reading the body
  into its own 400, so the middleware suppresses that response and sends **413
  `request_too_large`** itself. The endpoint re-checks the exact file size (413
  `file_too_large`).
* **Validation order:** size → magic bytes (415) → structure/encryption/page count (422) →
  admission: startup recovery done and embedding model loaded (503) → database. Nothing is
  written for a rejected file. Hashing runs in the same worker thread as validation.
* **Display names** additionally drop Unicode control/format characters (e.g. U+202E
  RIGHT-TO-LEFT OVERRIDE, which can disguise `invoice` + U+202E + `fdp.exe` as `invoiceexe.pdf`); added
  in the M1 audit.
* **Filenames are display-only:** directory parts (POSIX and Windows), control and reserved
  characters are stripped, NFC-normalised, capped at 255 characters keeping the extension.
  They are never used as paths.
* Error codes: `request_too_large`, `file_too_large` (413), `unsupported_media_type` (415),
  `empty_file`, `pdf_invalid`, `pdf_encrypted`, `pdf_too_many_pages` (422),
  `ingestion_not_ready`, `ingestion_queue_full` (503 + `Retry-After`).

## Consequences
* + Oversized uploads are cut off without spooling gigabytes; every rejection is specific.
* − The web UI's 20 MiB constant duplicates the API default (documented; the API is the
  authority).
* − No authentication or rate limiting yet (M5): anyone who can reach the loopback-bound API
  can upload. Mitigated by loopback binding, size/page caps and the bounded queue.
