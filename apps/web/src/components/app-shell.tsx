import Link from "next/link";
import { type ComponentType, type ReactNode, Suspense } from "react";

import { NavLink } from "@/components/nav-link";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-border bg-canvas/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5" aria-label="NEXUS AI home">
            <NexusMark />
            {/* Wordmark hidden on narrow screens so four nav links fit without overflow. */}
            <span className="hidden font-mono text-sm font-semibold tracking-[0.2em] text-foreground sm:inline">
              NEXUS<span className="text-accent">·AI</span>
            </span>
          </Link>
          <nav aria-label="Primary" className="flex min-w-0 items-center gap-0.5 text-sm sm:gap-1">
            {/* NavLink reads the pathname, which is request-time data on dynamic routes
                (/chat/[id]); the fallback renders the same links without highlighting. */}
            <Suspense fallback={<NavItems Item={PlainLink} />}>
              <NavItems Item={NavLink} />
            </Suspense>
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6 sm:py-14">
        {children}
      </main>
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 font-mono text-xs text-muted sm:px-6">
          <span>NEXUS AI</span>
          <span>M2 · grounded answers</span>
        </div>
      </footer>
    </div>
  );
}

const NAV_ITEMS = [
  ["/", "Overview"],
  ["/documents", "Documents"],
  ["/search", "Search"],
  ["/chat", "Chat"],
  ["/status", "Status"],
] as const;

type ItemProps = { href: string; children: ReactNode };

function NavItems({ Item }: { Item: ComponentType<ItemProps> }) {
  return (
    <>
      {NAV_ITEMS.map(([href, label]) => (
        <Item key={href} href={href}>
          {label}
        </Item>
      ))}
    </>
  );
}

function PlainLink({ href, children }: ItemProps) {
  return (
    <Link
      href={href}
      className="whitespace-nowrap rounded-md px-2 py-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-foreground sm:px-3"
    >
      {children}
    </Link>
  );
}

function NexusMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2 21 7v10l-9 5-9-5V7l9-5Z"
        stroke="currentColor"
        strokeWidth="1.5"
        className="text-accent"
      />
      <circle cx="12" cy="12" r="2.5" className="fill-accent" />
      <path d="M12 9.5V5M14.2 13.2 18 15.5M9.8 13.2 6 15.5" stroke="currentColor" strokeWidth="1.5" className="text-accent/70" />
    </svg>
  );
}
