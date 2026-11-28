"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { ConversationSummary } from "@/lib/api/types";

export function ConversationNav({ conversations }: { conversations: ConversationSummary[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Recent conversations">
      <ul className="max-h-[50vh] space-y-0.5 overflow-y-auto lg:max-h-[60vh]">
        {conversations.map((conversation) => {
          const href = `/chat/${conversation.id}`;
          const active = pathname === href;
          return (
            <li key={conversation.id}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`block truncate rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-accent-subtle text-accent"
                    : "text-muted hover:bg-surface-raised hover:text-foreground"
                }`}
                title={conversation.title}
              >
                {conversation.title}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
