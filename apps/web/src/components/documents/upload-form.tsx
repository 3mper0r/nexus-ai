"use client";

import { useActionState, useId, useRef, useState } from "react";

import { type UploadState, uploadAction } from "@/app/documents/actions";
import { formatBytes, MAX_UPLOAD_BYTES } from "@/lib/format";

const initialState: UploadState = { kind: "idle" };

export function UploadForm() {
  const [state, formAction, pending] = useActionState(uploadAction, initialState);
  const [selected, setSelected] = useState<File | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const hintId = useId();

  function choose(file: File | null) {
    setSelected(file);
    if (file && file.size > MAX_UPLOAD_BYTES) {
      setClientError(`${formatBytes(file.size)} exceeds the ${formatBytes(MAX_UPLOAD_BYTES)} limit.`);
    } else if (file && !file.name.toLowerCase().endsWith(".pdf") && file.type !== "application/pdf") {
      setClientError("Only PDF files are supported.");
    } else {
      setClientError(null);
    }
  }

  const message = clientError
    ? { tone: "error" as const, text: clientError }
    : state.kind === "error"
      ? { tone: "error" as const, text: state.message }
      : state.kind === "idle"
        ? null
        : { tone: "ok" as const, text: `${state.filename}: ${state.message}` };

  return (
    <form
      action={(formData) => {
        formAction(formData);
        setSelected(null);
        if (inputRef.current) inputRef.current.value = "";
      }}
      className="space-y-3"
    >
      <label
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files.item(0);
          if (file && inputRef.current) {
            const transfer = new DataTransfer();
            transfer.items.add(file);
            inputRef.current.files = transfer.files;
            choose(file);
          }
        }}
        // The real <input> is visually hidden, so keyboard focus is shown on the drop zone.
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-card border border-dashed px-6 py-10 text-center transition-colors has-[:focus-visible]:border-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent/60 ${
          dragging
            ? "border-accent bg-accent-subtle"
            : "border-border-strong bg-surface hover:border-accent/60 hover:bg-surface-raised"
        }`}
      >
        <UploadIcon />
        <span className="font-medium text-foreground">
          {selected ? selected.name : "Drop a PDF here or click to choose"}
        </span>
        <span id={hintId} className="text-sm text-muted">
          {selected
            ? formatBytes(selected.size)
            : `Text-based PDFs up to ${formatBytes(MAX_UPLOAD_BYTES)} · English, German and 90+ other languages`}
        </span>
        <input
          ref={inputRef}
          id={inputId}
          name="file"
          type="file"
          accept="application/pdf,.pdf"
          required
          aria-describedby={hintId}
          className="sr-only"
          onChange={(event) => choose(event.target.files?.item(0) ?? null)}
        />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={pending || !selected || clientError !== null}
          className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Uploading…" : "Upload and index"}
        </button>
        <p
          aria-live="polite"
          className={`text-sm ${message?.tone === "error" ? "text-danger" : "text-muted"}`}
        >
          {message?.text}
        </p>
      </div>
    </form>
  );
}

function UploadIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-accent">
      <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
