"use client";

import { useActionState } from "react";

import {
  type ConversationActionState,
  createConversationAction,
  deleteConversationAction,
} from "@/app/chat/actions";

const initial: ConversationActionState = { error: null };

export function NewConversationButton({ disabled = false }: { disabled?: boolean }) {
  const [state, action, pending] = useActionState(createConversationAction, initial);
  return (
    <form action={action} className="space-y-1">
      <button
        type="submit"
        disabled={disabled || pending}
        className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? "Creating…" : "New conversation"}
      </button>
      {state.error && (
        <p role="alert" className="text-xs text-danger">
          {state.error}
        </p>
      )}
    </form>
  );
}

export function DeleteConversationButton({ id, title }: { id: string; title: string }) {
  const [state, action, pending] = useActionState(deleteConversationAction.bind(null, id), initial);
  return (
    <form
      action={action}
      onSubmit={(event) => {
        if (!window.confirm(`Delete the conversation “${title}”?`)) event.preventDefault();
      }}
      className="flex flex-col items-end gap-1"
    >
      <button
        type="submit"
        disabled={pending}
        className="rounded-md px-2 py-1 text-xs text-muted transition-colors hover:bg-danger/10 hover:text-danger disabled:opacity-50"
      >
        {pending ? "Deleting…" : "Delete conversation"}
      </button>
      {state.error && (
        <span role="alert" className="text-xs text-danger">
          {state.error}
        </span>
      )}
    </form>
  );
}
