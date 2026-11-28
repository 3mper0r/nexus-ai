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

// --- Conversations and grounded answers (M2) -----------------------------------------

export const MESSAGE_STATUSES = [
  "streaming",
  "complete",
  "no_context",
  "truncated",
  "refused",
  "failed",
  "cancelled",
  "interrupted",
] as const;
export type MessageStatus = (typeof MESSAGE_STATUSES)[number];

export type Citation = {
  number: number;
  document_id: string;
  filename: string;
  page_start: number;
  page_end: number;
  chunk_ids: string[];
  text: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  citations: Citation[];
  grounded: boolean | null;
  model: string | null;
  input_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  stop_reason: string | null;
  stop_category: string | null;
  error_code: string | null;
  ttft_ms: number | null;
  total_ms: number | null;
  created_at: string;
};

export type ConversationSummary = { id: string; title: string; created_at: string; updated_at: string };
export type ConversationDetail = ConversationSummary & { messages: ChatMessage[] };

export type LlmStatus = {
  configured: boolean;
  ready: boolean;
  model: string;
  effort: string;
  max_output_tokens: number;
  daily_budget_usd: number;
  spent_today_usd: number | null; // null: database unavailable
  remaining_today_usd: number | null;
  active_generations: number;
  max_concurrent_generations: number;
};

const isNumberOrNullValue = (v: unknown): v is number | null => v === null || typeof v === "number";
const isStringOrNullValue = (v: unknown): v is string | null => v === null || typeof v === "string";

export function isCitation(value: unknown): value is Citation {
  return (
    isRecord(value) &&
    typeof value.number === "number" &&
    typeof value.document_id === "string" &&
    typeof value.filename === "string" &&
    typeof value.page_start === "number" &&
    typeof value.page_end === "number" &&
    Array.isArray(value.chunk_ids) &&
    value.chunk_ids.every((id) => typeof id === "string") &&
    typeof value.text === "string"
  );
}

export function isChatMessage(value: unknown): value is ChatMessage {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    (value.role === "user" || value.role === "assistant") &&
    typeof value.content === "string" &&
    MESSAGE_STATUSES.includes(value.status as MessageStatus) &&
    Array.isArray(value.citations) &&
    value.citations.every(isCitation) &&
    (value.grounded === null || typeof value.grounded === "boolean") &&
    isStringOrNullValue(value.model) &&
    isNumberOrNullValue(value.input_tokens) &&
    isNumberOrNullValue(value.cache_creation_tokens) &&
    isNumberOrNullValue(value.cache_read_tokens) &&
    isNumberOrNullValue(value.output_tokens) &&
    isNumberOrNullValue(value.cost_usd) &&
    isStringOrNullValue(value.stop_reason) &&
    isStringOrNullValue(value.stop_category) &&
    isStringOrNullValue(value.error_code) &&
    isNumberOrNullValue(value.ttft_ms) &&
    isNumberOrNullValue(value.total_ms) &&
    typeof value.created_at === "string"
  );
}

export function isConversationSummary(value: unknown): value is ConversationSummary {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

export function isConversationList(value: unknown): value is ConversationSummary[] {
  return Array.isArray(value) && value.every(isConversationSummary);
}

export function isConversationDetail(value: unknown): value is ConversationDetail {
  return (
    isConversationSummary(value) &&
    "messages" in value &&
    Array.isArray(value.messages) &&
    value.messages.every(isChatMessage)
  );
}

export function isLlmStatus(value: unknown): value is LlmStatus {
  return (
    isRecord(value) &&
    typeof value.configured === "boolean" &&
    typeof value.ready === "boolean" &&
    typeof value.model === "string" &&
    typeof value.effort === "string" &&
    typeof value.max_output_tokens === "number" &&
    typeof value.daily_budget_usd === "number" &&
    isNumberOrNullValue(value.spent_today_usd) &&
    isNumberOrNullValue(value.remaining_today_usd) &&
    typeof value.active_generations === "number" &&
    typeof value.max_concurrent_generations === "number"
  );
}

// Server-Sent Events from POST /api/v1/conversations/{id}/messages

export type AnswerUsage = {
  input_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  output_tokens: number | null;
};

export type StreamEvent =
  | {
      event: "meta";
      data: {
        conversation_id: string;
        user_message_id: string;
        message_id: string;
        model: string;
        sources: { document_id: string; filename: string; passages: number }[];
      };
    }
  | { event: "delta"; data: { text: string } }
  | { event: "citation"; data: Citation }
  | {
      event: "done";
      data: {
        message_id: string;
        status: MessageStatus;
        grounded: boolean | null;
        stop_reason?: string | null;
        stop_category?: string | null;
        usage?: AnswerUsage;
        cost_usd: number | null;
        ttft_ms?: number | null;
        total_ms?: number | null;
      };
    }
  | {
      event: "error";
      data: { message_id: string; code: string; message: string; retryable: boolean; retry_after: number | null };
    };

export function parseStreamEvent(name: string, raw: string): StreamEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;
  switch (name) {
    case "meta":
      return typeof data.message_id === "string" && Array.isArray(data.sources)
        ? ({ event: "meta", data } as StreamEvent)
        : null;
    case "delta":
      return typeof data.text === "string" ? { event: "delta", data: { text: data.text } } : null;
    case "citation":
      return isCitation(data) ? { event: "citation", data } : null;
    case "done":
      return typeof data.message_id === "string" &&
        MESSAGE_STATUSES.includes(data.status as MessageStatus)
        ? ({ event: "done", data } as StreamEvent)
        : null;
    case "error":
      return typeof data.code === "string" && typeof data.message === "string"
        ? ({ event: "error", data } as StreamEvent)
        : null;
    default:
      return null;
  }
}
