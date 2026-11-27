import "server-only";

import { apiRequest } from "./client";
import { type DocumentList, type DocumentOut, isDocumentList, isDocumentOut } from "./types";

/** Uploads stream up to 20 MiB and are validated by the API before it answers. */
const UPLOAD_TIMEOUT_MS = 30_000;

export function listDocuments(options: { limit?: number; cursor?: string } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit ?? 50) });
  if (options.cursor) params.set("cursor", options.cursor);
  return apiRequest<DocumentList>(`/api/v1/documents?${params}`, { guard: isDocumentList });
}

/** 202 = accepted for ingestion, 200 = identical file already known (deduplicated). */
export function uploadDocument(file: File, requestId?: string) {
  const form = new FormData();
  form.set("file", file, file.name);
  return apiRequest<DocumentOut>("/api/v1/documents", {
    method: "POST",
    body: form,
    guard: isDocumentOut,
    acceptStatuses: [200, 202],
    timeoutMs: UPLOAD_TIMEOUT_MS,
    requestId,
  });
}

export function deleteDocument(id: string) {
  return apiRequest<null>(`/api/v1/documents/${encodeURIComponent(id)}`, {
    method: "DELETE",
    acceptStatuses: [204],
  });
}
