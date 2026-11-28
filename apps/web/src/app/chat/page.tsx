import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Chat" };

export default function ChatIndexPage() {
  return (
    <div className="max-w-2xl space-y-6">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Ask your documents</h1>
        <p className="text-muted">
          Ask questions in English, German or another language. NEXUS retrieves the most relevant
          passages from your <Link href="/documents" className="text-accent hover:underline">documents</Link>,
          and Claude answers only from them, citing the exact pages. If the documents don&apos;t
          cover a question, it says so instead of guessing.
        </p>
      </header>
      <ul className="grid gap-3 text-sm sm:grid-cols-3">
        {[
          ["Grounded", "Every claim links to the passage and page it comes from."],
          ["Streaming", "Answers appear as they are written; stop them at any time."],
          ["Budgeted", "Token usage and cost are tracked against a daily limit."],
        ].map(([title, text]) => (
          <li key={title} className="rounded-card border border-border bg-surface p-4">
            <p className="font-medium text-foreground">{title}</p>
            <p className="mt-1 text-muted">{text}</p>
          </li>
        ))}
      </ul>
      <p className="rounded-card border border-warning/30 bg-warning/10 p-4 text-sm text-warning">
        Privacy: your question, the retrieved passages and recent turns of the conversation are
        sent to Anthropic&apos;s API. Search and embeddings stay on this machine. Use this feature
        with non-sensitive documents.
      </p>
      <p className="text-sm text-muted">Start a new conversation from the panel on the left.</p>
    </div>
  );
}
