"use client";

import { useState } from "react";
import { Play, Tv, Sparkles, Volume2, AlertTriangle } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";
import { GlowButton } from "@/components/atoms/GlowButton";
import { StatusBadge } from "@/components/atoms/StatusBadge";

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
    let warning: string | null = null;
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      } else {
        warning = "Fullscreen isn't supported in this browser — the show will run in windowed demo mode.";
      }
    } catch {
      warning = "Fullscreen was blocked. Allow it for this site, or continue in windowed demo mode.";
    }
    try {
      await (navigator as unknown as { wakeLock?: { request: (t: string) => Promise<unknown> } }).wakeLock?.request(
        "screen"
      );
    } catch {
      // non-fatal
    }
    setStarting(false);
    if (warning) {
      setFullscreenWarning(warning);
      return;
    }
    onStart();
  }

  return (
    <div
      className="fixed inset-0 flex flex-col items-center justify-center px-6 text-center bg-[var(--canvas-primary)] animate-fadeIn select-none"
    >
      <div className="max-w-md w-full p-8 sm:p-10 rounded-3xl glass-card backdrop-blur-2xl bg-slate-950/80 border border-white/10 shadow-2xl flex flex-col items-center animate-spring-in">
        <div className="w-20 h-20 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] flex items-center justify-center mb-6 border border-[var(--accent)]/30 shadow-[0_0_24px_rgba(99,102,241,0.25)]">
          <Tv className="w-10 h-10 stroke-[1.8]" />
        </div>

        <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
          {eventInfo?.name ?? "Showrunner Kiosk"}
        </h1>

        <div className="mb-8">
          <StatusBadge
            status={eventInfo ? "live" : "syncing"}
            label={eventInfo ? "LIVE BROADCAST READY" : "CONNECTING TO PRODUCER…"}
          />
        </div>

        <GlowButton
          variant="primary"
          size="lg"
          disabled={starting}
          onClick={() => void handleStart()}
          icon={Play}
          fullWidth
          className="shadow-2xl text-base"
        >
          {starting ? "Initializing Cinema Mode…" : "Start Fullscreen Show"}
        </GlowButton>

        <div className="flex items-center gap-2 mt-6 text-xs text-[var(--text-secondary)]">
          <Volume2 className="w-4 h-4 text-[var(--accent)]" />
          <span>Enables Lyria audio tracks & maintains Screen Wake Lock</span>
        </div>

        {fullscreenWarning && (
          <div className="flex flex-col items-center gap-2 mt-6 pt-4 border-t border-white/10 w-full animate-spring-in">
            <div className="flex items-center gap-2 text-xs text-amber-400">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{fullscreenWarning}</span>
            </div>
            <button
              type="button"
              onClick={onStart}
              className="text-xs underline text-[var(--text-secondary)] hover:text-white cursor-pointer"
            >
              Continue in windowed mode
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
