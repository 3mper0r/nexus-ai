"use client";

import { useActionState } from "react";

import { type DeleteState, deleteAction } from "@/app/documents/actions";

export function DeleteButton({ id, filename, disabled }: { id: string; filename: string; disabled: boolean }) {
  const [state, formAction, pending] = useActionState(deleteAction.bind(null, id), {
    error: null,
  } satisfies DeleteState);

  return (
    <form
      action={formAction}
      onSubmit={(event) => {
        if (!window.confirm(`Delete “${filename}” and all of its indexed passages?`)) {
          event.preventDefault();
        }
      }}
      className="flex flex-col items-end gap-1"
    >
      <button
        type="submit"
        disabled={disabled || pending}
        title={disabled ? "Wait until processing has finished" : `Delete ${filename}`}
        className="rounded-md px-2 py-1 text-xs text-muted transition-colors hover:bg-danger/10 hover:text-danger disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted"
      >
        {pending ? "Deleting…" : "Delete"}
        <span className="sr-only"> {filename}</span>
      </button>
      {state.error && (
        <span role="alert" className="text-xs text-danger">
          {state.error}
        </span>
      )}
    </form>
  );
}
