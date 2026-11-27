"""Expected failures of the document and search features.

Subclasses of ``AppError`` (domain errors that carry an HTTP mapping but are raised
without any knowledge of the transport layer).
"""

from __future__ import annotations

from nexus_api.core.errors import AppError


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class EmptyFileError(AppError):
    status_code = 422
    code = "empty_file"


class PdfInvalidError(AppError):
    status_code = 422
    code = "pdf_invalid"


class PdfEncryptedError(AppError):
    status_code = 422
    code = "pdf_encrypted"


class PdfTooManyPagesError(AppError):
    status_code = 422
    code = "pdf_too_many_pages"


class DocumentNotFoundError(AppError):
    status_code = 404
    code = "document_not_found"


class DocumentBusyError(AppError):
    status_code = 409
    code = "document_busy"


class InvalidCursorError(AppError):
    status_code = 422
    code = "invalid_cursor"


class IngestionQueueFullError(AppError):
    status_code = 503
    code = "ingestion_queue_full"


class IngestionNotReadyError(AppError):
    status_code = 503
    code = "ingestion_not_ready"
