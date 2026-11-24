import "server-only";

import { getServerEnv } from "@/lib/env.server";

import { type ApiResult, type Guard, isApiErrorBody } from "./types";

type GetOptions<T> = {
  guard: Guard<T>;
  /** HTTP statuses whose body is a valid T (e.g. readiness returns 503 + report). */
  acceptStatuses?: readonly number[];
  requestId?: string;
};

/**
 * Server-side GET against the NEXUS API.
 *
 * Never throws for transport or protocol failures: every outcome is a typed
 * ApiResult, so callers must handle backend unavailability explicitly.
 * Every request is bounded by API_TIMEOUT_MS and bypasses the Next.js data cache.
 */
export async function apiGet<T>(path: string, options: GetOptions<T>): Promise<ApiResult<T>> {
  const { apiBaseUrl, apiTimeoutMs } = getServerEnv();
  const { guard, acceptStatuses = [200], requestId } = options;
  const url = new URL(path, apiBaseUrl);
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);

  let response: Response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(apiTimeoutMs),
      headers: {
        Accept: "application/json",
        ...(requestId ? { "X-Request-ID": requestId } : {}),
      },
    });
  } catch (error) {
    return { ok: false, error: toTransportFailure(error, apiTimeoutMs), durationMs: elapsed() };
  }

  const responseRequestId = response.headers.get("x-request-id");
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (isTimeout(error)) {
      return { ok: false, error: toTransportFailure(error, apiTimeoutMs), durationMs: elapsed() };
    }
    body = undefined;
  }

  if (!acceptStatuses.includes(response.status)) {
    return {
      ok: false,
      error: {
        kind: "http",
        status: response.status,
        requestId: responseRequestId,
        message: isApiErrorBody(body)
          ? body.error.message
          : `Unexpected HTTP ${response.status} from API.`,
      },
      durationMs: elapsed(),
    };
  }

  if (!guard(body)) {
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
    data: body,
    status: response.status,
    requestId: responseRequestId,
    durationMs: elapsed(),
  };
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
