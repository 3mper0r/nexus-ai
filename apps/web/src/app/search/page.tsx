import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { type ReactNode, Suspense } from "react";

import { ResultCard } from "@/components/search/result-card";
import { listDocuments } from "@/lib/api/documents";
import { searchDocuments } from "@/lib/api/search";
import type { DocumentOut } from "@/lib/api/types";

export const metadata: Metadata = { title: "Search" };

const TOP_K_OPTIONS = [5, 10, 20] as const;
const EXAMPLES = [
  "How long is customer data retained?",
  "Wie viele Urlaubstage habe ich?",
  "Was gilt im Brandfall?",
];

type Params = { q: string; k: number; doc: string | null };

function parseParams(raw: Record<string, string | string[] | undefined>): Params {
  const first = (value: string | string[] | undefined) =>
    (Array.isArray(value) ? value[0] : value)?.trim() ?? "";
  const k = Number(first(raw.k));
  const doc = first(raw.doc);
  return {
    q: first(raw.q).slice(0, 1000),
    k: TOP_K_OPTIONS.includes(k as (typeof TOP_K_OPTIONS)[number]) ? k : 5,
    doc: /^[0-9a-f-]{36}$/i.test(doc) ? doc : null,
  };
}

export default function SearchPage({ searchParams }: PageProps<"/search">) {
  return (
    <div className="space-y-8">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Search</h1>
        <p className="text-muted">
          Semantic search across your documents. Queries match by meaning, not keywords, and
          work across languages: ask in German, find English passages, and vice versa.
        </p>
      </header>
      {/* Search params are request-time data: everything that reads them streams in. */}
      <Suspense fallback={<SearchSkeleton />}>
        <SearchPanel searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function SearchPanel({ searchParams }: Pick<PageProps<"/search">, "searchParams">) {
  const params = parseParams(await searchParams);
  const library = await listDocuments({ limit: 100 });
  const ready = library.ok ? library.data.items.filter((d) => d.status === "ready") : [];

  return (
    <div className="space-y-8">
      <SearchForm params={params} documents={ready} />
      {!library.ok ? (
        <Notice tone="danger" title="The API is unavailable.">
          {library.error.message}
        </Notice>
      ) : ready.length === 0 ? (
        <Notice tone="neutral" title="Nothing to search yet.">
          <Link href="/documents" className="text-accent hover:underline">
            Upload a PDF
          </Link>{" "}
          and wait for it to be ready.
        </Notice>
      ) : params.q ? (
        <Results params={params} documents={ready} />
      ) : (
        <Examples />
      )}
    </div>
  );
}

function SearchForm({ params, documents }: { params: Params; documents: DocumentOut[] }) {
  return (
    <Form action="/search" className="space-y-3" role="search">
      <div className="flex flex-col gap-3 sm:flex-row">
        <label htmlFor="q" className="sr-only">
          Search query
        </label>
        <input
          id="q"
          name="q"
          type="search"
          defaultValue={params.q}
          placeholder="Ask a question in any language…"
          maxLength={1000}
          required
          autoComplete="off"
          className="min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-4 py-2.5 text-foreground placeholder:text-muted/70 focus:border-accent focus:outline-none"
        />
        <button
          type="submit"
          className="rounded-md bg-accent px-5 py-2.5 text-sm font-medium text-on-accent transition-colors hover:bg-accent-strong"
        >
          Search
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm text-muted">
        <label className="flex items-center gap-2">
          <span>Scope</span>
          <select
            name="doc"
            defaultValue={params.doc ?? ""}
            className="max-w-64 truncate rounded-md border border-border bg-surface px-2 py-1 text-foreground"
          >
            <option value="">All documents</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span>Results</span>
          <select
            name="k"
            defaultValue={String(params.k)}
            className="rounded-md border border-border bg-surface px-2 py-1 text-foreground"
          >
            {TOP_K_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
      </div>
    </Form>
  );
}

async function Results({ params, documents }: { params: Params; documents: DocumentOut[] }) {
  const scoped = params.doc ? documents.find((d) => d.id === params.doc) : undefined;
  const result = await searchDocuments({
    query: params.q,
    topK: params.k,
    documentIds: params.doc ? [params.doc] : undefined,
  });

  if (!result.ok) {
    const loading = result.error.code === "embedding_model_unavailable";
    return (
      <Notice tone={loading ? "warning" : "danger"} title={loading ? "The search model is not ready." : "Search failed."}>
        {result.error.message}
        {loading && " It loads a few seconds after the API starts; run `make models` if it never becomes ready."}
      </Notice>
    );
  }

  const { results, took_ms, embedding_model } = result.data;
  return (
    <section aria-labelledby="results-heading" className="space-y-4">
      <h2 id="results-heading" className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
        {results.length} {results.length === 1 ? "passage" : "passages"}
        {scoped && ` in ${scoped.filename}`} · {Math.round(took_ms)} ms
      </h2>
      {results.length === 0 ? (
        <Notice tone="neutral" title="No passages found.">
          Try rephrasing the question or widening the scope.
        </Notice>
      ) : (
        <ol className="space-y-3">
          {results.map((r, index) => (
            <li key={r.chunk_id}>
              <ResultCard result={r} rank={index + 1} />
            </li>
          ))}
        </ol>
      )}
      <p className="font-mono text-xs text-muted">
        Ranked by cosine similarity · {embedding_model.split("@")[0]}
      </p>
    </section>
  );
}

function Examples() {
  return (
    <section aria-labelledby="examples-heading" className="space-y-3">
      <h2 id="examples-heading" className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
        Try asking
      </h2>
      <ul className="flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <li key={example}>
            <Link
              href={`/search?q=${encodeURIComponent(example)}`}
              className="inline-block rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/60 hover:text-foreground"
            >
              {example}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

const NOTICE_TONES = {
  danger: "border-danger/30 bg-danger/10 text-danger",
  warning: "border-warning/30 bg-warning/10 text-warning",
  neutral: "border-border bg-surface text-muted",
} as const;

function Notice({
  tone,
  title,
  children,
}: {
  tone: keyof typeof NOTICE_TONES;
  title: string;
  children: ReactNode;
}) {
  return (
    <div role={tone === "neutral" ? "status" : "alert"} className={`rounded-card border p-4 text-sm ${NOTICE_TONES[tone]}`}>
      <p className="font-medium">{title}</p>
      <p className="mt-1 opacity-90">{children}</p>
    </div>
  );
}

function SearchSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading search">
      <div className="h-11 animate-pulse rounded-md border border-border bg-surface" />
      <div className="h-32 animate-pulse rounded-card border border-border bg-surface" />
    </div>
  );
}
