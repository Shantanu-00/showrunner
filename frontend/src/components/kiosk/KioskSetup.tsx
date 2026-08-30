"use client";

import { useState } from "react";
import { Play, Tv, Sparkles, Volume2, AlertTriangle } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";

export function KioskSetup({
  eventInfo,
  onStart,
}: {
  eventInfo: EventPublicInfo | null;
  onStart: () => void;
}) {
  const [starting, setStarting] = useState(false);
  const [fullscreenWarning, setFullscreenWarning] = useState<string | null>(null);

  async function handleStart() {
    setStarting(true);
    setFullscreenWarning(null);
    try {
      const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      const source = ctx.createBufferSource();
      source.buffer = ctx.createBuffer(1, 1, 22050);
      source.connect(ctx.destination);
      source.start(0);
    } catch {
      // non-fatal
    }
    // B4: this used to be optional-chained and silent on refusal. Every layout below assumes
    // fullscreen (the QR/monogram and status glyph are pinned in viewport-relative corners), so a
    // laptop that reaches "started" without actually going fullscreen needs to be told why, not left
    // to guess at a cramped, overlapping show.
    let warning: string | null = null;
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      } else {
        warning = "Fullscreen isn't supported in this browser — the show will run in the current window.";
      }
    } catch {
      warning = "Fullscreen was blocked. Allow it for this site, or continue in windowed mode.";
    }
    try {
      await (navigator as unknown as { wakeLock?: { request: (t: string) => Promise<unknown> } }).wakeLock?.request(
        "screen"
      );
    } catch {
      // non-fatal — no wake lock support/grant; the screen may sleep, but the show still runs
    }
    setStarting(false);
    if (warning) {
      // Stay on this screen rather than call onStart(): KioskSetup unmounts the moment it does, so
      // this is the only chance to show the operator why the wall isn't actually fullscreen.
      setFullscreenWarning(warning);
      return;
    }
    onStart();
  }

  return (
    <div
      className="fixed inset-0 flex flex-col items-center justify-center px-8 text-center bg-[var(--bg-0)] animate-fadeIn"
    >
      <div className="max-w-md p-10 rounded-3xl glass-card border border-white/10 shadow-2xl flex flex-col items-center">
        <div className="w-20 h-20 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center mb-6 border border-[var(--gold-500)]/30">
          <Tv className="w-10 h-10 stroke-[1.8]" />
        </div>

        <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
          {eventInfo?.name ?? "Showrunner Kiosk"}
        </h1>

        <div className="flex items-center gap-2 mb-8">
          <span className="live-dot" />
          <span className="text-xs font-mono font-semibold uppercase tracking-wider text-[var(--ok)]">
            {eventInfo ? "Live Event Feed Ready" : "Connecting to Producer Booth…"}
          </span>
        </div>

        <button
          type="button"
          disabled={starting}
          onClick={() => void handleStart()}
          className="btn-primary w-full py-4 px-8 text-base font-semibold flex items-center justify-center gap-2 shadow-2xl disabled:opacity-50"
        >
          <Play className="w-5 h-5 fill-current" />
          <span>{starting ? "Initializing Cinema Mode…" : "Start Fullscreen Show"}</span>
        </button>

        <div className="flex items-center gap-2 mt-6 text-xs text-[var(--ink-muted)]">
          <Volume2 className="w-4 h-4 text-[var(--gold-300)]" />
          <span>Enables Lyria audio tracks & maintains Screen Wake Lock</span>
        </div>

        {fullscreenWarning && (
          <div className="flex flex-col items-center gap-2 mt-6 pt-4 border-t border-white/10 w-full">
            <div className="flex items-center gap-2 text-xs text-[var(--warn)]">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{fullscreenWarning}</span>
            </div>
            <button
              type="button"
              onClick={onStart}
              className="text-xs underline text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            >
              Continue anyway
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
