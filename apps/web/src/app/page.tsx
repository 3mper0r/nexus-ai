import Link from "next/link";

const foundation = [
  {
    name: "API",
    detail: "FastAPI · Pydantic · structured logging · request correlation",
  },
  {
    name: "Data",
    detail: "PostgreSQL 17 · pgvector 0.8 · SQLAlchemy async · Alembic",
  },
  {
    name: "Web",
    detail: "Next.js App Router · strict TypeScript · Tailwind CSS",
  },
  {
    name: "Retrieval",
    detail: "pypdf extraction · multilingual-e5-small · HNSW cosine search · page citations",
  },
  {
    name: "Quality",
    detail: "Ruff · mypy strict · pytest · ESLint · GitHub Actions",
  },
] as const;

export default function HomePage() {
  return (
    <div className="space-y-14">
      <section className="max-w-3xl space-y-6">
        <p className="font-mono text-xs uppercase tracking-[0.25em] text-accent">
          Milestone 1 · Ingestion &amp; retrieval
        </p>
        <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          The engineering foundation for{" "}
          <span className="text-accent">NEXUS AI</span>.
        </h1>
        <p className="text-lg text-pretty text-muted">
          Upload PDFs and search them by meaning, in English, German and dozens of other
          languages, with every result cited to its page. Grounded answers from Claude
          build on this in the next milestone.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/documents"
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-accent-strong"
          >
            Upload documents <span aria-hidden="true">→</span>
          </Link>
          <Link
            href="/search"
            className="inline-flex items-center gap-2 rounded-md border border-border-strong px-4 py-2 text-sm font-medium text-foreground transition-colors hover:border-accent/60 hover:bg-surface-raised"
          >
            Search
          </Link>
        </div>
      </section>

      <section aria-labelledby="foundation-heading" className="space-y-4">
        <h2 id="foundation-heading" className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Platform layers
        </h2>
        <ul className="grid gap-4 sm:grid-cols-2">
          {foundation.map((layer) => (
            <li key={layer.name} className="rounded-card border border-border bg-surface p-5">
              <h3 className="font-medium text-foreground">{layer.name}</h3>
              <p className="mt-1 text-sm text-muted">{layer.detail}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
