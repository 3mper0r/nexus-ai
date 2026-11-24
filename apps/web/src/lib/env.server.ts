import "server-only";

/**
 * Server-only environment configuration.
 *
 * These variables are deliberately NOT prefixed with NEXT_PUBLIC_, so Next.js
 * never inlines them into client bundles, and the `server-only` import makes any
 * accidental import from a Client Component a build error.
 *
 * Values are read lazily (at request time, not at module load) so `next build`
 * succeeds without them and a deployment can change them without rebuilding.
 */

export type ServerEnv = {
  apiBaseUrl: URL;
  apiTimeoutMs: number;
};

// 127.0.0.1 rather than "localhost": Node 17+ may resolve localhost to ::1
// first, while uvicorn binds IPv4 by default.
const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const DEFAULT_API_TIMEOUT_MS = 3000;

export function getServerEnv(): ServerEnv {
  return {
    apiBaseUrl: parseBaseUrl(process.env.API_BASE_URL ?? DEFAULT_API_BASE_URL),
    apiTimeoutMs: parseTimeout(process.env.API_TIMEOUT_MS),
  };
}

function parseBaseUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`API_BASE_URL is not a valid URL: ${JSON.stringify(raw)}`);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`API_BASE_URL must use http or https, got ${url.protocol}`);
  }
  return url;
}

function parseTimeout(raw: string | undefined): number {
  if (raw === undefined || raw === "") return DEFAULT_API_TIMEOUT_MS;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 100 || value > 30_000) {
    throw new Error(`API_TIMEOUT_MS must be an integer between 100 and 30000, got ${raw}`);
  }
  return value;
}
