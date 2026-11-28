import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";

import { ChatThread } from "@/components/chat/chat-thread";
import { DeleteConversationButton } from "@/components/chat/conversation-buttons";
import { getConversation, getLlmStatus } from "@/lib/api/conversations";

export const metadata: Metadata = { title: "Conversation" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default function ConversationPage({ params }: PageProps<"/chat/[id]">) {
  return (
    <Suspense fallback={<ThreadSkeleton />}>
      <Conversation params={params} />
    </Suspense>
  );
}

async function Conversation({ params }: Pick<PageProps<"/chat/[id]">, "params">) {
  await connection();
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  const [conversation, status] = await Promise.all([getConversation(id), getLlmStatus()]);

  if (!conversation.ok) {
    if (conversation.error.status === 404) notFound();
    return (
      <div role="alert" className="rounded-card border border-danger/30 bg-danger/10 p-4 text-sm text-danger">
        <p className="font-medium">The conversation could not be loaded.</p>
        <p className="mt-1 opacity-90">{conversation.error.message}</p>
      </div>
    );
  }

  const configured = status.ok && status.data.configured;
  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <h1 className="min-w-0 truncate text-xl font-semibold tracking-tight" title={conversation.data.title}>
          {conversation.data.title}
        </h1>
        <DeleteConversationButton id={id} title={conversation.data.title} />
      </header>
      {!configured && (
        <p role="status" className="rounded-card border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
          Grounded answers need an Anthropic API key. Set <code className="font-mono">ANTHROPIC_API_KEY</code>{" "}
          in <code className="font-mono">.env</code> and restart the API. Search keeps working without it.
        </p>
      )}
      <ChatThread
        key={id}
        conversationId={id}
        initialMessages={conversation.data.messages}
        configured={configured}
      />
    </div>
  );
}

function ThreadSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading conversation">
      <div className="h-7 w-64 animate-pulse rounded bg-surface-raised" />
      <div className="h-40 animate-pulse rounded-card border border-border bg-surface" />
    </div>
  );
}
