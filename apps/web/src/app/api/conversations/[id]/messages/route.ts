import { getServerEnv } from "@/lib/env.server";

/**
 * Streaming proxy: browser -> Next.js server -> NEXUS API (ADR 0019).
 *
 * The browser never learns the API's address. The upstream request is tied to the
 * browser request's abort signal, so pressing "Stop" (or closing the tab) closes
 * the upstream connection and the API stops generating (no further tokens billed).
 *
 * Route Handlers do not get Server Actions' built-in CSRF check, so this handler
 * requires a JSON body and a same-origin Origin header: a cross-site page cannot
 * make the browser spend the API budget.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_QUESTION_CHARS = 4000;

/** Host of the Origin header; null when absent or opaque ("null" from sandboxed or
 * privacy-restricted contexts), which must be rejected (403), not crash (500). */
function originHost(origin: string | null): string | null {
  if (!origin || !URL.canParse(origin)) return null;
  return new URL(origin).host;
}

function error(status: number, code: string, message: string) {
  return Response.json({ error: { code, message, request_id: "web" } }, { status });
}

export async function POST(request: Request, ctx: RouteContext<"/api/conversations/[id]/messages">) {
  const { id } = await ctx.params;
  if (!UUID.test(id)) return error(422, "validation_error", "Invalid conversation id.");

  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!host || originHost(request.headers.get("origin")) !== host) {
    return error(403, "forbidden_origin", "Cross-origin requests are not allowed.");
  }
  if (!request.headers.get("content-type")?.startsWith("application/json")) {
    return error(415, "unsupported_media_type", "Expected application/json.");
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return error(400, "invalid_json", "The request body is not valid JSON.");
  }
  const content =
    typeof payload === "object" && payload !== null && "content" in payload ? payload.content : undefined;
  const documentIds =
    typeof payload === "object" && payload !== null && "document_ids" in payload
      ? payload.document_ids
      : undefined;
  if (typeof content !== "string" || !content.trim() || content.length > MAX_QUESTION_CHARS) {
    return error(422, "validation_error", `Ask a question of 1 to ${MAX_QUESTION_CHARS} characters.`);
  }
  if (
    documentIds !== undefined &&
    documentIds !== null &&
    !(Array.isArray(documentIds) && documentIds.length <= 50 && documentIds.every((d) => typeof d === "string" && UUID.test(d)))
  ) {
    return error(422, "validation_error", "Invalid document scope.");
  }

  const { apiBaseUrl } = getServerEnv();
  let upstream: Response;
  try {
    upstream = await fetch(new URL(`/api/v1/conversations/${id}/messages`, apiBaseUrl), {
      method: "POST",
      cache: "no-store",
      signal: request.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "X-Request-ID": `web-${crypto.randomUUID()}`,
      },
      body: JSON.stringify({ content, document_ids: documentIds ?? null }),
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return error(502, "api_unreachable", "The NEXUS API is unreachable.");
  }

  const contentType = upstream.headers.get("content-type") ?? "";
  if (!upstream.ok || !contentType.startsWith("text/event-stream") || upstream.body === null) {
    // Admission errors (no key, budget, busy, not found) are JSON envelopes: pass through.
    const headers = new Headers({ "Content-Type": contentType || "application/json" });
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers.set("Retry-After", retryAfter);
    return new Response(await upstream.text(), { status: upstream.status, headers });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
