# ADR 0001: Monorepo with independent app toolchains

* Status: Accepted (M0, 2026-11-24)

## Context
NEXUS AI has a Python backend and a TypeScript frontend that evolve together
(API contracts, CI, docs) but use unrelated toolchains.

## Decision
A single repository with `apps/api` (uv project) and `apps/web` (npm project).
No JS monorepo tool (Turborepo/Nx) and no uv workspace: each app owns its lockfile.
A root `Makefile` is the shared command surface; `compose.yaml`, `.env.example`,
CI and docs live at the root.

## Consequences
* + Atomic changes across API + UI + docs in one commit/PR; one CI pipeline.
* + Each app remains independently buildable/containerisable.
* − No automatic cross-language type sharing: TS types in `apps/web/src/lib/api/types.ts`
  mirror Pydantic models by hand. *Revisit* when the API surface grows: generate a
  TS client from FastAPI's OpenAPI schema.
* Rejected: polyrepo (coordination overhead for a single team); Turborepo/Nx (adds
  tooling for only one JS package).
