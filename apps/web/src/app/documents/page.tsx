import type { Metadata } from "next";
import { Suspense } from "react";

import { DocumentLibrary, DocumentLibrarySkeleton } from "@/components/documents/document-library";
import { UploadForm } from "@/components/documents/upload-form";

export const metadata: Metadata = { title: "Documents" };

export default function DocumentsPage() {
  return (
    <div className="space-y-10">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Documents</h1>
        <p className="text-muted">
          Upload PDFs to index them for semantic search. Each page is split into passages and
          embedded with a multilingual model, so you can search in English or German and get
          answers back with page citations.
        </p>
      </header>

      <UploadForm />

      {/* Static shell above is prerendered; the library streams in per request. */}
      <Suspense fallback={<DocumentLibrarySkeleton />}>
        <DocumentLibrary />
      </Suspense>
    </div>
  );
}
