import "server-only";

import { getServerEnv } from "@/lib/env.server";

import { type ApiResult, type Guard, isApiErrorBody } from "./types";

type RequestOptions<T> = {
  method?: "GET" | "POST" | "DELETE";
  /** JSON-serialisable object, or FormData for multipart uploads. */
  body?: FormData | Record<string, unknown>;
  /** Runtime guard for the response body. Omit for responses without a body (204). */
  guard?: Guard<T>;
  /** HTTP statuses whose body is a valid T (e.g. readiness returns 503 + report). */
  acceptStatuses?: readonly number[];
  requestId?: string;
  /** Overrides API_TIMEOUT_MS for slow operations such as uploads. */
  timeoutMs?: number;
};

/**
 * Server-side request against the NEXUS API.
 *
 * Never throws for transport or protocol failures: every outcome is a typed
 * ApiResult, so callers must handle backend unavailability explicitly.
 * Every request is time-bounded and bypasses the Next.js data cache.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions<T> = {},
): Promise<ApiResult<T>> {
  const { apiBaseUrl, apiTimeoutMs } = getServerEnv();
  const {
    method = "GET",
    body,
    guard,
    acceptStatuses = [200],
    requestId,
    timeoutMs = apiTimeoutMs,
  } = options;
  const url = new URL(path, apiBaseUrl);
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);

  const headers: Record<string, string> = { Accept: "application/json" };
  if (requestId) headers["X-Request-ID"] = requestId;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // fetch sets the multipart boundary itself
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      body: payload,
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
      headers,
    });
  } catch (error) {
    return { ok: false, error: toTransportFailure(error, timeoutMs), durationMs: elapsed() };
  }

  const responseRequestId = response.headers.get("x-request-id");
  let data: unknown;
  if (response.status !== 204) {
    try {
      data = await response.json();
    } catch (error) {
      if (isTimeout(error)) {
        return { ok: false, error: toTransportFailure(error, timeoutMs), durationMs: elapsed() };
      }
      data = undefined;
    }
  }

  if (!acceptStatuses.includes(response.status)) {
    return {
      ok: false,
      error: {
        kind: "http",
        status: response.status,
        requestId: responseRequestId,
        code: isApiErrorBody(data) ? data.error.code : undefined,
        message: isApiErrorBody(data)
          ? data.error.message
          : `Unexpected HTTP ${response.status} from API.`,
      },
      durationMs: elapsed(),
    };
  }

  if (guard && !guard(data)) {
    return {
      ok: false,
      error: {
        kind: "invalid_response",
        status: response.status,
        requestId: responseRequestId,
        message: "API response did not match the expected contract.",
      },
      durationMs: elapsed(),
    };
  }

  return {
    ok: true,
    // Without a guard the caller asked for no body (T is `null`).
    data: (guard ? data : null) as T,
    status: response.status,
    requestId: responseRequestId,
    durationMs: elapsed(),
  };
}

type GetOptions<T> = {
  guard: Guard<T>;
  acceptStatuses?: readonly number[];
  requestId?: string;
};

export function apiGet<T>(path: string, options: GetOptions<T>): Promise<ApiResult<T>> {
  return apiRequest(path, { ...options, method: "GET" });
}

function isTimeout(error: unknown): boolean {
  return error instanceof DOMException && error.name === "TimeoutError";
}

function toTransportFailure(error: unknown, timeoutMs: number) {
  if (isTimeout(error)) {
    return { kind: "timeout" as const, message: `API did not respond within ${timeoutMs} ms.` };
  }
  // Undici reports DNS/connection failures as TypeError("fetch failed", { cause }).
  const cause = error instanceof Error && error.cause instanceof Error ? error.cause : undefined;
  const code = cause && "code" in cause && typeof cause.code === "string" ? cause.code : undefined;
  return {
    kind: "network" as const,
    message: code ? `API is unreachable (${code}).` : "API is unreachable.",
  };
}
