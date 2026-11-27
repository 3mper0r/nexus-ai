"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/**
 * Status polling: while any document is pending or processing, re-render the
 * server components every `intervalMs`. Stops as soon as everything settled, and
 * pauses while the tab is hidden.
 */
export function AutoRefresh({ active, intervalMs = 2000 }: { active: boolean; intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs, router]);

  if (!active) return null;
  return (
    <span className="inline-flex items-center gap-2 font-mono text-xs text-muted" aria-live="polite">
      <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden="true" />
      Processing, updating live
    </span>
  );
}
