# ADR 0007: Server-side API access from Next.js with Cache Components

* Status: Accepted (M0, 2026-11-24)

## Decision
* Next.js 16.4 App Router with the generator defaults `cacheComponents` and
  `partialPrefetching` enabled.
* The status page is a Server Component: a static, prerendered shell plus a
  `<Suspense>`-wrapped panel that calls `await connection()` and fetches at request
  time with `cache: "no-store"` and `AbortSignal.timeout(API_TIMEOUT_MS)`.
* API configuration (`API_BASE_URL`, `API_TIMEOUT_MS`) is **server-only**: no
  `NEXT_PUBLIC_` prefix, parsed lazily at request time, modules guarded with
  `import "server-only"`.
* `apiGet<T>()` returns a discriminated `ApiResult<T>` and validates responses with
  runtime type guards; it never throws for transport/protocol failures.
* TypeScript pinned to 5.9.3 and ESLint to 9.x because `typescript-eslint` supports
  TypeScript < 6.1 and the Next ESLint plugins target ESLint 9.
* Fonts self-hosted via the `geist` package (no build-time network fetch).

## Consequences
* + `next build` never needs a running backend (verified with the API stopped).
* + Internal API address never reaches the browser (verified: 0 matches in `.next/static`).
* + Backend outages and timeouts render as UI states, not error pages (verified).
* − Browser-direct API calls (SSE later) will need a public URL variable and CORS (already configured).
* Rejected: client-side `useEffect` fetching (exposes API URL, adds a loading waterfall);
  `export const dynamic = "force-dynamic"` (not compatible with Cache Components).
