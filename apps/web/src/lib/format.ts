import type { DocumentStatus } from "@/lib/api/types";

/**
 * Must match the API's MAX_UPLOAD_BYTES default (20 MiB). The API enforces the
 * real limit; this copy only lets the browser reject oversized files early.
 */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const timestamp = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatTimestamp(iso: string): string {
  return timestamp.format(new Date(iso));
}

export function formatPages(start: number, end: number): string {
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`;
}

export const STATUS_TONE: Record<DocumentStatus, "success" | "danger" | "warning" | "neutral"> = {
  pending: "neutral",
  processing: "warning",
  ready: "success",
  failed: "danger",
};

const FAILURE_MESSAGES: Record<string, string> = {
  no_extractable_text:
    "No text layer found. Scanned PDFs need OCR, which is not supported yet.",
  extraction_failed: "The PDF could not be parsed.",
  embedding_failed: "Computing embeddings failed.",
  text_limit_exceeded:
    "The PDF contains far more text than its page count suggests and was not indexed.",
  interrupted: "Processing was interrupted by an API restart.",
  internal_error: "An unexpected processing error occurred.",
};

export function failureMessage(code: string | null): string {
  return (code && FAILURE_MESSAGES[code]) ?? "Processing failed.";
}
