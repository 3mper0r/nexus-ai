"use server";

import { redirect } from "next/navigation";

import { createConversation, deleteConversation } from "@/lib/api/conversations";

export type ConversationActionState = { error: string | null };

export async function createConversationAction(): Promise<ConversationActionState> {
  const result = await createConversation();
  if (!result.ok) return { error: result.error.message };
  redirect(`/chat/${result.data.id}`);
}

export async function deleteConversationAction(id: string): Promise<ConversationActionState> {
  const result = await deleteConversation(id);
  if (!result.ok) return { error: result.error.message };
  redirect("/chat");
}
