import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";

import { StatusBadge } from "@/components/status-badge";
import { getLiveness, getReadiness } from "@/lib/api/health";
import type { ApiResult, DependencyCheck, ReadinessResponse } from "@/lib/api/types";

export const metadata: Metadata = { title: "System status" };

export default function StatusPage() {
  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">System status</h1>
        <p className="text-muted">
          Live probes against the NEXUS API, evaluated on the server for every request.
        </p>
      </header>
      {/* The static shell above is prerendered; the panel streams in at request time. */}
      <Suspense fallback={<StatusSkeleton />}>
        <HealthPanel />
      </Suspense>
    </div>
  );
}

async function HealthPanel() {
  // Opt out of prerendering: this must reflect the backend *now*, never build time.
  await connection();
  const requestId = `web-${crypto.randomUUID()}`;
  const [liveness, readiness] = await Promise.all([
    getLiveness(requestId),
    getReadiness(requestId),
  ]);
  const checkedAt = new Date().toISOString();

  return (
    <div className="space-y-4">
      {!liveness.ok && (
        <div
          role="alert"
          className="rounded-card border border-danger/30 bg-danger/10 p-4 text-sm text-danger"
        >
          <p className="font-medium">The API is unavailable.</p>
          <p className="mt-1 text-danger/90">
            {liveness.error.message} Start it with <code className="font-mono">make api</code>.
          </p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <ProbeCard
          title="API process"
          description="Liveness: the HTTP process is up and serving requests."
          badge={
            liveness.ok
              ? { tone: "success", label: "live" }
              : { tone: "danger", label: liveness.error.kind }
          }
          rows={[
            ["Endpoint", "GET /api/v1/health/live"],
            ["Round trip", `${liveness.durationMs} ms`],
            ["Request ID", liveness.ok ? (liveness.requestId ?? "n/a") : "n/a"],
          ]}
        />
        <DatabaseCard readiness={readiness} />
      </div>

      <p className="font-mono text-xs text-muted">
        Checked at <time dateTime={checkedAt}>{checkedAt}</time> · correlation{" "}
        <span className="text-foreground">{requestId}</span>
      </p>
    </div>
  );
}

function DatabaseCard({ readiness }: { readiness: ApiResult<ReadinessResponse> }) {
  if (!readiness.ok) {
    return (
      <ProbeCard
        title="Database"
        description="Readiness: PostgreSQL reachable within the health-check timeout."
        badge={{ tone: "neutral", label: "unknown" }}
        rows={[
          ["Endpoint", "GET /api/v1/health/ready"],
          ["Reason", readiness.error.message],
        ]}
      />
    );
  }

  const database: DependencyCheck | undefined = readiness.data.checks.database;
  const isUp = database?.status === "up";
  return (
    <ProbeCard
      title="Database"
      description="Readiness: PostgreSQL reachable within the health-check timeout."
      badge={isUp ? { tone: "success", label: "ready" } : { tone: "warning", label: "not ready" }}
      rows={[
        ["Endpoint", `GET /api/v1/health/ready → ${readiness.status}`],
        ["Query latency", database?.latency_ms != null ? `${database.latency_ms} ms` : "n/a"],
        ["Failure", database?.error ?? "none"],
      ]}
    />
  );
}

type ProbeCardProps = {
  title: string;
  description: string;
  badge: { tone: "success" | "danger" | "warning" | "neutral"; label: string };
  rows: ReadonlyArray<readonly [string, string]>;
};

function ProbeCard({ title, description, badge, rows }: ProbeCardProps) {
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label={title}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium">{title}</h2>
          <p className="mt-1 text-sm text-muted">{description}</p>
        </div>
        <StatusBadge tone={badge.tone} label={badge.label} />
      </div>
      <dl className="mt-5 space-y-2 border-t border-border pt-4 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4">
            <dt className="text-muted">{label}</dt>
            <dd className="truncate text-right font-mono text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function StatusSkeleton() {
  return (
    <div className="grid gap-4 md:grid-cols-2" aria-busy="true" aria-label="Loading status">
      {[0, 1].map((key) => (
        <div key={key} className="h-48 animate-pulse rounded-card border border-border bg-surface" />
      ))}
    </div>
  );
}
