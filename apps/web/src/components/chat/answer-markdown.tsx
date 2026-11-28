"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Citation } from "@/lib/api/types";
import { formatPages } from "@/lib/format";

const CITE_HREF = /^#cite-(\d{1,3})$/;

/**
 * Renders an answer's Markdown safely (D7):
 * * raw HTML in the model output is never rendered: react-markdown turns it into
 *   plain text (React escapes it). It is shown literally rather than dropped, because
 *   dropping a CommonMark HTML block also drops the ordinary text on the same lines;
 * * images are not rendered at all (an image URL would make the browser fetch an
 *   arbitrary external address);
 * * URLs pass react-markdown's default sanitiser (no `javascript:` links);
 * * citation markers `[n](#cite-n)`, inserted by the API only for validated citations,
 *   become chips that link to the message's source list.
 */
export function AnswerMarkdown({
  content,
  citations,
  messageId,
}: {
  content: string;
  citations: Citation[];
  messageId: string;
}) {
  const byNumber = new Map(citations.map((c) => [c.number, c]));

  const components: Components = {
    a({ href, children }) {
      const match = href?.match(CITE_HREF);
      if (match) {
        const citation = byNumber.get(Number(match[1]));
        if (!citation) return null; // only validated citations are linkable
        return (
          <a
            href={`#source-${messageId}-${citation.number}`}
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-1 items-center justify-center rounded border border-accent/40 bg-accent-subtle px-1 font-mono text-[10px] leading-none text-accent no-underline hover:bg-accent hover:text-on-accent"
            title={`${citation.filename}, ${formatPages(citation.page_start, citation.page_end)}`}
            aria-label={`Source ${citation.number}: ${citation.filename}, ${formatPages(citation.page_start, citation.page_end)}`}
          >
            {citation.number}
          </a>
        );
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-accent underline underline-offset-2">
          {children}
        </a>
      );
    },
    img: () => null,
    h1: ({ children }) => <Heading level={1}>{children}</Heading>,
    h2: ({ children }) => <Heading level={2}>{children}</Heading>,
    h3: ({ children }) => <Heading level={3}>{children}</Heading>,
    h4: ({ children }) => <Heading level={3}>{children}</Heading>,
    p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
    ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-accent/50 pl-3 text-muted">{children}</blockquote>
    ),
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border-b border-border-strong px-3 py-1.5 font-medium text-foreground">{children}</th>
    ),
    td: ({ children }) => <td className="border-b border-border px-3 py-1.5 align-top">{children}</td>,
    pre: ({ children }) => (
      <pre className="my-3 overflow-x-auto rounded-md border border-border bg-canvas p-3 font-mono text-xs leading-relaxed">
        {children}
      </pre>
    ),
    code: ({ className, children }) =>
      className ? (
        <code className={className}>{children}</code>
      ) : (
        <code className="rounded bg-surface-raised px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
      ),
    hr: () => <hr className="my-4 border-border" />,
  };

  return (
    <div className="text-sm text-foreground/95">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml={false} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

function Heading({ level, children }: { level: 1 | 2 | 3; children: ReactNode }) {
  const size = level === 1 ? "text-base" : level === 2 ? "text-[0.95rem]" : "text-sm";
  const Tag = level === 1 ? "h3" : level === 2 ? "h4" : "h5"; // answers sit under the page's h1/h2
  return <Tag className={`mb-1.5 mt-3 font-semibold text-foreground first:mt-0 ${size}`}>{children}</Tag>;
}
