import "server-only";

import { apiRequest } from "./client";
import { isSearchResponse, type SearchResponse } from "./types";

/** Query embedding waits behind an in-flight ingestion batch (~1-2 s worst case). */
const SEARCH_TIMEOUT_MS = 10_000;

export function searchDocuments(input: { query: string; topK: number; documentIds?: string[] }) {
  return apiRequest<SearchResponse>("/api/v1/search", {
    method: "POST",
    body: {
      query: input.query,
      top_k: input.topK,
      ...(input.documentIds?.length ? { document_ids: input.documentIds } : {}),
    },
    guard: isSearchResponse,
    timeoutMs: SEARCH_TIMEOUT_MS,
  });
}
