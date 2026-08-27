import type { Metadata, Viewport } from "next";
import { fraunces, inter, jetbrainsMono } from "@/design/fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "Showrunner",
  description: "Your event's own agent-directed photo show.",
  manifest: "/manifest.json",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0b0709",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      data-theme="wedding_hindu"
      className={`${fraunces.variable} ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <body className="font-[var(--font-ui)] antialiased">{children}</body>
    </html>
  );
}
