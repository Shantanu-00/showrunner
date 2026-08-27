import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";

// next/font/google self-hosts at build time — no runtime request to Google,
// satisfying spec 12 §11's "no external font CDN" rule.
export const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-display",
  display: "swap",
});

export const inter = Inter({
  subsets: ["latin"],
  variable: "--font-ui",
  display: "swap",
});

export const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});
