import { connection } from "next/server";
import { type ReactNode, Suspense } from "react";

import { NewConversationButton } from "@/components/chat/conversation-buttons";
import { ConversationNav } from "@/components/chat/conversation-nav";
import { getLlmStatus, listConversations } from "@/lib/api/conversations";

export default function ChatLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start" aria-label="Conversations">
        <Suspense fallback={<SidebarSkeleton />}>
          <Sidebar />
        </Suspense>
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

async function Sidebar() {
  await connection();
  const [conversations, status] = await Promise.all([listConversations(), getLlmStatus()]);
  const configured = status.ok && status.data.configured;

  return (
    <>
      <NewConversationButton disabled={!conversations.ok} />
      {!conversations.ok ? (
        <p role="alert" className="text-sm text-danger">
          {conversations.error.message}
        </p>
      ) : conversations.data.length === 0 ? (
        <p className="text-sm text-muted">No conversations yet.</p>
      ) : (
        <ConversationNav conversations={conversations.data} />
      )}
      {status.ok && (
        <div className="space-y-2 rounded-card border border-border bg-surface p-3 text-xs text-muted">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em]">Answer model</p>
          {configured ? (
            <>
              <p className="font-mono text-foreground">
                {status.data.model} · effort {status.data.effort}
              </p>
              <BudgetMeter spent={status.data.spent_today_usd} budget={status.data.daily_budget_usd} />
            </>
          ) : (
            <p>
              Not configured. Set <code className="font-mono text-foreground">ANTHROPIC_API_KEY</code> in{" "}
              <code className="font-mono text-foreground">.env</code> and restart the API.
            </p>
          )}
        </div>
      )}
    </>
  );
}

function BudgetMeter({ spent, budget }: { spent: number | null; budget: number }) {
  if (spent === null) {
    return <p>Today&apos;s spend is unavailable (database unreachable).</p>;
  }
  const ratio = budget > 0 ? Math.min(1, spent / budget) : 1;
  const tone = ratio >= 0.9 ? "bg-danger" : ratio >= 0.6 ? "bg-warning" : "bg-accent";
  return (
    <div className="space-y-1">
      <div className="flex justify-between">
        <span>Today</span>
        <span className="font-mono text-foreground">
          ${spent.toFixed(3)} / ${budget.toFixed(2)}
        </span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-surface-raised"
        role="meter"
        aria-label="Daily API budget used"
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={spent}
      >
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

function SidebarSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading conversations">
      <div className="h-9 animate-pulse rounded-md bg-surface-raised" />
      <div className="h-24 animate-pulse rounded-card border border-border bg-surface" />
    </div>
  );
}
