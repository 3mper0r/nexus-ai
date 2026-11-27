/**
 * Response contracts for the NEXUS API (mirrors apps/api Pydantic models) plus
 * runtime guards. TypeScript types vanish at runtime, so data crossing the
 * network boundary is narrowed by a guard before it is trusted.
 */

export type LivenessResponse = { status: "ok" };

export type DependencyCheck = {
  status: "up" | "down";
  latency_ms: number | null;
  error: "timeout" | "unavailable" | "loading" | null;
};

export type ReadinessResponse = {
  status: "ready" | "not_ready";
  checks: Record<string, DependencyCheck>;
};

export type ApiErrorBody = {
  error: { code: string; message: string; request_id: string };
};

export type ApiFailureKind = "timeout" | "network" | "http" | "invalid_response";

export type ApiFailure = {
  kind: ApiFailureKind;
  message: string;
  status?: number;
  /** Stable machine-readable error code from the API envelope, when present. */
  code?: string;
  requestId?: string | null;
};

export type ApiResult<T> =
  | { ok: true; data: T; status: number; requestId: string | null; durationMs: number }
  | { ok: false; error: ApiFailure; durationMs: number };

export type Guard<T> = (value: unknown) => value is T;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isLivenessResponse(value: unknown): value is LivenessResponse {
  return isRecord(value) && value.status === "ok";
}

function isDependencyCheck(value: unknown): value is DependencyCheck {
  return (
    isRecord(value) &&
    (value.status === "up" || value.status === "down") &&
    (value.latency_ms === null || typeof value.latency_ms === "number") &&
    (value.error === null ||
      value.error === "timeout" ||
      value.error === "unavailable" ||
      value.error === "loading")
  );
}

export function isReadinessResponse(value: unknown): value is ReadinessResponse {
  return (
    isRecord(value) &&
    (value.status === "ready" || value.status === "not_ready") &&
    isRecord(value.checks) &&
    Object.values(value.checks).every(isDependencyCheck)
  );
}

export function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    typeof value.error.code === "string" &&
    typeof value.error.message === "string"
  );
}

// --- Documents (M1) ----------------------------------------------------------------

export const DOCUMENT_STATUSES = ["pending", "processing", "ready", "failed"] as const;
export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];

export type DocumentOut = {
  id: string;
  filename: string;
  status: DocumentStatus;
  error_code: string | null;
  size_bytes: number;
  page_count: number;
  chunk_count: number | null;
  attempts: number;
  embedding_model: string | null;
  sha256: string;
  created_at: string;
  updated_at: string;
};

export type DocumentList = { items: DocumentOut[]; next_cursor: string | null };

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNumberOrNull(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

export function isDocumentOut(value: unknown): value is DocumentOut {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.filename === "string" &&
    DOCUMENT_STATUSES.includes(value.status as DocumentStatus) &&
    isStringOrNull(value.error_code) &&
    typeof value.size_bytes === "number" &&
    typeof value.page_count === "number" &&
    isNumberOrNull(value.chunk_count) &&
    typeof value.attempts === "number" &&
    isStringOrNull(value.embedding_model) &&
    typeof value.sha256 === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

export function isDocumentList(value: unknown): value is DocumentList {
  return (
    isRecord(value) &&
    Array.isArray(value.items) &&
    value.items.every(isDocumentOut) &&
    isStringOrNull(value.next_cursor)
  );
}

// --- Search (M1) -------------------------------------------------------------------

export type SearchResult = {
  chunk_id: string;
  document_id: string;
  filename: string;
  ordinal: number;
  page_start: number;
  page_end: number;
  text: string;
  score: number;
};

export type SearchResponse = {
  query: string;
  results: SearchResult[];
  embedding_model: string;
  took_ms: number;
};

function isSearchResult(value: unknown): value is SearchResult {
  return (
    isRecord(value) &&
    typeof value.chunk_id === "string" &&
    typeof value.document_id === "string" &&
    typeof value.filename === "string" &&
    typeof value.ordinal === "number" &&
    typeof value.page_start === "number" &&
    typeof value.page_end === "number" &&
    typeof value.text === "string" &&
    typeof value.score === "number"
  );
}

export function isSearchResponse(value: unknown): value is SearchResponse {
  return (
    isRecord(value) &&
    typeof value.query === "string" &&
    Array.isArray(value.results) &&
    value.results.every(isSearchResult) &&
    typeof value.embedding_model === "string" &&
    typeof value.took_ms === "number"
  );
}
