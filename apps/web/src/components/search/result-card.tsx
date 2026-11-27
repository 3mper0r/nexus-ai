import Link from "next/link";

import type { SearchResult } from "@/lib/api/types";
import { formatPages } from "@/lib/format";

export function ResultCard({ result, rank }: { result: SearchResult; rank: number }) {
  const percent = Math.max(0, Math.min(100, Math.round(result.score * 100)));
  return (
    <article
      aria-labelledby={`result-${result.chunk_id}`}
      className="rounded-card border border-border bg-surface p-5 transition-colors hover:border-border-strong"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-muted">#{rank}</p>
          <h3 id={`result-${result.chunk_id}`} className="truncate font-medium text-foreground">
            <Link
              href={`/search?doc=${result.document_id}`}
              className="hover:text-accent"
              title={`Search only in ${result.filename}`}
            >
              {result.filename}
            </Link>
          </h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="rounded-full border border-accent/40 bg-accent-subtle px-2.5 py-0.5 font-mono text-xs text-accent">
            {formatPages(result.page_start, result.page_end)}
          </span>
          <div className="flex items-center gap-2" title="Cosine similarity between query and passage">
            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-raised" aria-hidden="true">
              <span className="block h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
            </span>
            <span className="font-mono text-xs text-muted">
              <span className="sr-only">similarity </span>
              {result.score.toFixed(3)}
            </span>
          </div>
        </div>
      </header>
      <p className="mt-3 line-clamp-6 whitespace-pre-line text-sm leading-relaxed text-foreground/90">
        {result.text}
      </p>
    </article>
  );
}
