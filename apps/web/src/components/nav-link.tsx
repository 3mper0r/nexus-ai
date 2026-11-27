"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname();
  const isActive = href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <Link
      href={href}
      aria-current={isActive ? "page" : undefined}
      className={`whitespace-nowrap rounded-md px-2 py-1.5 transition-colors sm:px-3 ${
        isActive
          ? "bg-accent-subtle text-accent"
          : "text-muted hover:bg-surface-raised hover:text-foreground"
      }`}
    >
      {children}
    </Link>
  );
}
