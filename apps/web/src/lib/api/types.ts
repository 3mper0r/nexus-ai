/**
 * Response contracts for the NEXUS API (mirrors apps/api Pydantic models) plus
 * runtime guards. TypeScript types vanish at runtime, so data crossing the
 * network boundary is narrowed by a guard before it is trusted.
 */

export type LivenessResponse = { status: "ok" };

export type DependencyCheck = {
  status: "up" | "down";
  latency_ms: number | null;
  error: "timeout" | "unavailable" | null;
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
    (value.error === null || value.error === "timeout" || value.error === "unavailable")
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
