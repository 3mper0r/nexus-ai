import Link from "next/link";
import { connection } from "next/server";

import { AutoRefresh } from "@/components/documents/auto-refresh";
import { DeleteButton } from "@/components/documents/delete-button";
import { StatusBadge } from "@/components/status-badge";
import { listDocuments } from "@/lib/api/documents";
import type { DocumentOut } from "@/lib/api/types";
import { failureMessage, formatBytes, formatTimestamp, STATUS_TONE } from "@/lib/format";

export async function DocumentLibrary() {
  // Always reflect the database now, never build time.
  await connection();
  const result = await listDocuments({ limit: 100 });

  if (!result.ok) {
    return (
      <div role="alert" className="rounded-card border border-danger/30 bg-danger/10 p-4 text-sm text-danger">
        <p className="font-medium">The document library could not be loaded.</p>
        <p className="mt-1 text-danger/90">{result.error.message}</p>
      </div>
    );
  }

  const documents = result.data.items;
  const active = documents.some((d) => d.status === "pending" || d.status === "processing");
  const ready = documents.filter((d) => d.status === "ready");

  return (
    <section aria-labelledby="library-heading" className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="library-heading" className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Library · {documents.length} {documents.length === 1 ? "document" : "documents"}
          {ready.length > 0 && ` · ${ready.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0)} passages indexed`}
        </h2>
        <AutoRefresh active={active} />
      </div>

      {documents.length === 0 ? (
        <div className="rounded-card border border-border bg-surface p-8 text-center text-sm text-muted">
          No documents yet. Upload a PDF above to make it searchable.
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-card border border-border bg-surface">
          {documents.map((document) => (
            <DocumentRow key={document.id} document={document} />
          ))}
        </ul>
      )}

      {result.data.next_cursor && (
        <p className="text-xs text-muted">Showing the 100 most recent documents.</p>
      )}
    </section>
  );
}

function DocumentRow({ document }: { document: DocumentOut }) {
  const busy = document.status === "pending" || document.status === "processing";
  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <PdfIcon />
          <span className="truncate font-medium text-foreground" title={document.filename}>
            {document.filename}
          </span>
          <StatusBadge tone={STATUS_TONE[document.status]} label={document.status} />
        </div>
        <p className="font-mono text-xs text-muted">
          {document.page_count} {document.page_count === 1 ? "page" : "pages"} ·{" "}
          {formatBytes(document.size_bytes)}
          {document.chunk_count !== null && ` · ${document.chunk_count} passages`}
          {document.attempts > 1 && ` · attempt ${document.attempts}`} · updated{" "}
          <time dateTime={document.updated_at}>{formatTimestamp(document.updated_at)}</time>
        </p>
        {document.status === "failed" && (
          <p className="text-sm text-danger">
            {failureMessage(document.error_code)}{" "}
            <span className="text-muted">Upload the same file again to retry.</span>
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {document.status === "ready" && (
          <Link
            href={`/search?doc=${document.id}`}
            className="rounded-md px-2 py-1 text-xs text-accent transition-colors hover:bg-accent-subtle"
          >
            Search in document
          </Link>
        )}
        <DeleteButton id={document.id} filename={document.filename} disabled={busy} />
      </div>
    </li>
  );
}

function PdfIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0 text-muted">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M14 3v5h5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function DocumentLibrarySkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading documents">
      <div className="h-4 w-48 animate-pulse rounded bg-surface-raised" />
      <div className="h-40 animate-pulse rounded-card border border-border bg-surface" />
    </div>
  );
}
