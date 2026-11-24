import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";

import { AppShell } from "@/components/app-shell";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "NEXUS AI", template: "%s · NEXUS AI" },
  description: "NEXUS AI: an AI engineering platform.",
};

export const viewport: Viewport = {
  themeColor: "#070b10",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="min-h-dvh font-sans antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
