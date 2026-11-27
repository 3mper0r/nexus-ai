"use server";

import { refresh } from "next/cache";

import { deleteDocument, uploadDocument } from "@/lib/api/documents";
import { formatBytes, MAX_UPLOAD_BYTES } from "@/lib/format";

export type UploadState =
  | { kind: "idle" }
  | { kind: "accepted" | "duplicate"; message: string; filename: string }
  | { kind: "error"; message: string };

/**
 * Forwards the upload to the API from the Next.js server (ADR 0007: the browser
 * never talks to the API directly). The API is the authority on validation; the
 * checks here only avoid a pointless round trip.
 */
export async function uploadAction(_previous: UploadState, formData: FormData): Promise<UploadState> {
  const file = formData.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { kind: "error", message: "Choose a PDF file to upload." };
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      kind: "error",
      message: `The file is ${formatBytes(file.size)}; the limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`,
    };
  }

  const result = await uploadDocument(file, `web-${crypto.randomUUID()}`);
  if (!result.ok) {
    return { kind: "error", message: result.error.message };
  }
  refresh();
  if (result.status === 200) {
    return {
      kind: "duplicate",
      filename: result.data.filename,
      message:
        result.data.status === "ready"
          ? "This file is already in the library. Nothing was re-processed."
          : "This file is already being processed.",
    };
  }
  return {
    kind: "accepted",
    filename: result.data.filename,
    message:
      result.data.attempts > 1
        ? `Retrying ingestion (attempt ${result.data.attempts}).`
        : "Uploaded. Text extraction and embedding run in the background.",
  };
}

export type DeleteState = { error: string | null };

export async function deleteAction(id: string): Promise<DeleteState> {
  const result = await deleteDocument(id);
  if (!result.ok) {
    return { error: result.error.message };
  }
  refresh();
  return { error: null };
}
