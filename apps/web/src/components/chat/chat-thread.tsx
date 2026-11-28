"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";

import { AnswerMarkdown } from "@/components/chat/answer-markdown";
import { type ChatMessage, type Citation, isApiErrorBody, parseStreamEvent } from "@/lib/api/types";
import { formatPages } from "@/lib/format";
import { readServerSentEvents } from "@/lib/sse";

const MAX_QUESTION_CHARS = 4000;

type Props = {
  conversationId: string;
  initialMessages: ChatMessage[];
  configured: boolean;
};

type Notice = { tone: "error" | "warning"; text: string } | null;

function placeholderMessage(role: "user" | "assistant", content = ""): ChatMessage {
  return {
    id: `local-${crypto.randomUUID()}`,
    role,
    content,
    status: role === "user" ? "complete" : "streaming",
    citations: [],
    grounded: null,
    model: null,
    input_tokens: null,
    cache_creation_tokens: null,
    cache_read_tokens: null,
    output_tokens: null,
    cost_usd: null,
    stop_reason: null,
    stop_category: null,
    error_code: null,
    ttft_ms: null,
    total_ms: null,
    created_at: new Date().toISOString(),
  };
}

export function ChatThread({ conversationId, initialMessages, configured }: Props) {
  const router = useRouter();
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [announcement, setAnnouncement] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const pendingText = useRef("");
  const frame = useRef<number | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateAssistant = useCallback((update: (m: ChatMessage) => ChatMessage) => {
    setMessages((current) => {
      const index = current.findLastIndex((m) => m.role === "assistant");
      const target = current[index];
      if (index === -1 || target === undefined) return current;
      const next = current.slice();
      next[index] = update(target);
      return next;
    });
  }, []);

  // Text deltas are batched once per animation frame instead of re-rendering the
  // Markdown for every token.
  const flushText = useCallback(() => {
    frame.current = null;
    const text = pendingText.current;
    pendingText.current = "";
    if (text) updateAssistant((m) => ({ ...m, content: m.content + text }));
  }, [updateAssistant]);

  const queueText = useCallback(
    (text: string) => {
      pendingText.current += text;
      if (frame.current === null) frame.current = requestAnimationFrame(flushText);
    },
    [flushText],
  );

  async function ask(text: string) {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setNotice(null);
    setAnnouncement("Generating an answer…");
    const userMessage = placeholderMessage("user", text);
    setMessages((current) => [...current, userMessage, placeholderMessage("assistant")]);

    let response: Response;
    try {
      response = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
        signal: controller.signal,
      });
    } catch {
      rollback(userMessage.id, text, controller.signal.aborted ? null : "The server could not be reached.");
      return;
    }

    if (!response.ok || !response.body) {
      // Admission errors (no key, budget, busy...): nothing was stored server-side.
      let message = `Request failed (HTTP ${response.status}).`;
      try {
        const body: unknown = await response.json();
        if (isApiErrorBody(body)) message = body.error.message;
      } catch {
        /* keep the generic message */
      }
      rollback(userMessage.id, text, message);
      return;
    }

    try {
      for await (const raw of readServerSentEvents(response.body)) {
        const event = parseStreamEvent(raw.event, raw.data);
        if (!event) continue;
        switch (event.event) {
          case "meta":
            updateAssistant((m) => ({ ...m, id: event.data.message_id, model: event.data.model }));
            break;
          case "delta":
            queueText(event.data.text);
            break;
          case "citation": {
            const citation: Citation = event.data;
            updateAssistant((m) => ({ ...m, citations: [...m.citations, citation] }));
            break;
          }
          case "done": {
            flushText();
            const done = event.data;
            updateAssistant((m) => ({
              ...m,
              status: done.status,
              grounded: done.grounded,
              stop_category: done.stop_category ?? null,
              cost_usd: done.cost_usd,
              input_tokens: done.usage?.input_tokens ?? null,
              cache_read_tokens: done.usage?.cache_read_tokens ?? null,
              cache_creation_tokens: done.usage?.cache_creation_tokens ?? null,
              output_tokens: done.usage?.output_tokens ?? null,
              ttft_ms: done.ttft_ms ?? null,
              total_ms: done.total_ms ?? null,
            }));
            setAnnouncement("Answer complete.");
            break;
          }
          case "error":
            flushText();
            updateAssistant((m) => ({ ...m, status: "failed", error_code: event.data.code }));
            setNotice({ tone: "error", text: event.data.message });
            setAnnouncement("The answer could not be completed.");
            break;
        }
      }
    } catch {
      flushText();
      if (controller.signal.aborted) {
        updateAssistant((m) => ({ ...m, status: "cancelled" }));
        setAnnouncement("Stopped.");
      } else {
        updateAssistant((m) => ({ ...m, status: "failed", error_code: "connection_lost" }));
        setNotice({ tone: "error", text: "The connection was lost while the answer was streaming." });
      }
    } finally {
      flushText();
      setStreaming(false);
      abortRef.current = null;
      router.refresh(); // conversation list: title and ordering
    }
  }

  function rollback(userMessageId: string, text: string, message: string | null) {
    setMessages((current) => current.filter((m) => m.id !== userMessageId).slice(0, -1));
    setQuestion(text);
    setStreaming(false);
    abortRef.current = null;
    setAnnouncement("");
    if (message) setNotice({ tone: "warning", text: message });
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || streaming) return;
    setQuestion("");
    void ask(text);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="flex min-h-[60vh] flex-col">
      <div role="log" aria-label="Conversation" className="flex-1 space-y-6 pb-6">
        {messages.length === 0 && (
          <div className="rounded-card border border-dashed border-border-strong p-8 text-center text-sm text-muted">
            Ask a question about your documents. Answers cite the exact pages they rely on.
          </div>
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <div key={message.id} className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap rounded-card rounded-br-sm bg-accent-subtle px-4 py-2.5 text-sm text-foreground">
                {message.content}
              </p>
            </div>
          ) : (
            <AssistantMessage key={message.id} message={message} />
          ),
        )}
        <div ref={endRef} />
      </div>

      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      <div className="sticky bottom-0 -mx-1 space-y-2 bg-canvas/95 px-1 pb-2 pt-3 backdrop-blur">
        {notice && (
          <p
            role="alert"
            className={`rounded-md border px-3 py-2 text-sm ${
              notice.tone === "error"
                ? "border-danger/30 bg-danger/10 text-danger"
                : "border-warning/30 bg-warning/10 text-warning"
            }`}
          >
            {notice.text}
          </p>
        )}
        <form onSubmit={onSubmit} className="flex items-end gap-2">
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <textarea
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={MAX_QUESTION_CHARS}
            disabled={!configured}
            placeholder={configured ? "Ask in any language… (Enter to send, Shift+Enter for a new line)" : "Grounded answers are not configured."}
            className="min-h-[3rem] flex-1 resize-y rounded-md border border-border-strong bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted/70 focus:border-accent focus:outline-none disabled:opacity-60"
          />
          {streaming ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              className="h-10 rounded-md border border-danger/50 px-4 text-sm font-medium text-danger transition-colors hover:bg-danger/10"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!configured || !question.trim()}
              className="h-10 rounded-md bg-accent px-4 text-sm font-medium text-on-accent transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
            >
              Ask
            </button>
          )}
        </form>
        <p className="text-xs text-muted">
          Questions, the retrieved passages and recent turns of this conversation are sent to
          Anthropic&apos;s API to generate answers. Use non-sensitive documents.
        </p>
      </div>
    </div>
  );
}

const STATUS_NOTES: Partial<Record<ChatMessage["status"], { tone: "warning" | "danger" | "muted"; text: string }>> = {
  truncated: { tone: "warning", text: "The answer was cut off at the output limit." },
  refused: { tone: "warning", text: "Claude declined to answer this request." },
  failed: { tone: "danger", text: "The answer could not be completed." },
  cancelled: { tone: "muted", text: "Stopped. The partial answer is kept." },
  interrupted: { tone: "danger", text: "Interrupted by a server restart. Ask again to continue." },
};

function AssistantMessage({ message }: { message: ChatMessage }) {
  const note = STATUS_NOTES[message.status];
  const isStreaming = message.status === "streaming";
  return (
    <article aria-busy={isStreaming} className="space-y-3 rounded-card border border-border bg-surface p-4 sm:p-5">
      {message.content ? (
        <AnswerMarkdown content={message.content} citations={message.citations} messageId={message.id} />
      ) : isStreaming ? (
        <p className="flex items-center gap-2 text-sm text-muted">
          <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden="true" />
          Searching your documents and thinking…
        </p>
      ) : null}

      {note && (
        <p
          className={`text-xs ${note.tone === "danger" ? "text-danger" : note.tone === "warning" ? "text-warning" : "text-muted"}`}
        >
          {note.text}
          {message.status === "refused" && message.stop_category ? ` (category: ${message.stop_category})` : ""}
          {message.status === "failed" && message.error_code ? ` (${message.error_code})` : ""}
        </p>
      )}
      {message.status === "complete" && message.grounded === false && (
        <p className="text-xs text-warning">
          No sources were cited for this answer. Treat it with caution.
        </p>
      )}

      {message.citations.length > 0 && <SourcesList citations={message.citations} messageId={message.id} />}

      {!isStreaming && (message.cost_usd !== null || message.output_tokens !== null) && (
        <p className="font-mono text-[11px] text-muted">
          {message.input_tokens !== null && `${message.input_tokens + (message.cache_read_tokens ?? 0) + (message.cache_creation_tokens ?? 0)} in`}
          {message.cache_read_tokens ? ` (${message.cache_read_tokens} cached)` : ""}
          {message.output_tokens !== null && ` · ${message.output_tokens} out`}
          {message.cost_usd !== null && ` · $${message.cost_usd.toFixed(4)}`}
          {message.total_ms !== null && ` · ${(message.total_ms / 1000).toFixed(1)} s`}
        </p>
      )}
    </article>
  );
}

function SourcesList({ citations, messageId }: { citations: Citation[]; messageId: string }) {
  return (
    <section aria-label="Sources" className="border-t border-border pt-3">
      <h4 className="mb-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted">Sources</h4>
      <ol className="space-y-2">
        {citations.map((citation) => (
          <li key={citation.number} id={`source-${messageId}-${citation.number}`} className="scroll-mt-24 text-sm">
            <details className="group rounded-md border border-border bg-canvas/60 open:border-border-strong">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 marker:hidden">
                <span className="inline-flex size-5 shrink-0 items-center justify-center rounded border border-accent/40 bg-accent-subtle font-mono text-[10px] text-accent">
                  {citation.number}
                </span>
                <span className="truncate text-foreground">{citation.filename}</span>
                <span className="shrink-0 rounded-full border border-accent/40 px-2 py-0.5 font-mono text-[11px] text-accent">
                  {formatPages(citation.page_start, citation.page_end)}
                </span>
                <span className="ml-auto text-xs text-muted group-open:hidden">Show passage</span>
              </summary>
              <div className="space-y-2 border-t border-border px-3 py-2">
                <p className="whitespace-pre-line text-xs leading-relaxed text-foreground/85">{citation.text}</p>
                <Link href={`/search?doc=${citation.document_id}`} className="text-xs text-accent hover:underline">
                  Search in this document
                </Link>
              </div>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}
