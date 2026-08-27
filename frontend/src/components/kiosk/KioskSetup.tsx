"use client";

import { useState } from "react";
import type { EventPublicInfo } from "@/lib/types";

/** Operator setup screen (spec 12 §5.3): one tap unlocks audio (for reel premieres) and
 * acquires the Screen Wake Lock (spec 04 §4) before the fullscreen show starts. */
export function KioskSetup({
  eventInfo,
  onStart,
}: {
  eventInfo: EventPublicInfo | null;
  onStart: () => void;
}) {
  const [starting, setStarting] = useState(false);

  async function handleStart() {
    setStarting(true);
    try {
      // Audio unlock: a real, if silent, playback inside this user gesture is what browsers
      // check for — resuming an AudioContext alone doesn't reliably unlock <video> autoplay.
      const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      const source = ctx.createBufferSource();
      source.buffer = ctx.createBuffer(1, 1, 22050);
      source.connect(ctx.destination);
      source.start(0);
    } catch {
      // non-fatal — some browsers don't need this, none of them throw usefully
    }
    try {
      await document.documentElement.requestFullscreen?.();
    } catch {
      // fullscreen can be denied (e.g. iOS Safari) — the show still runs, just not fullscreen
    }
    try {
      await (navigator as unknown as { wakeLock?: { request: (t: string) => Promise<unknown> } }).wakeLock?.request(
        "screen"
      );
    } catch {
      // Wake Lock unsupported — acceptable degradation, not a blocker
    }
    onStart();
  }

  return (
    <div
      className="fixed inset-0 flex flex-col items-center justify-center px-8 text-center"
      style={{ background: "var(--bg-0)" }}
    >
      <p className="font-[var(--font-display)] text-4xl mb-2" style={{ color: "var(--ivory)" }}>
        {eventInfo?.name ?? "Showrunner"}
      </p>
      <p className="text-sm mb-10" style={{ color: eventInfo ? "var(--ok)" : "var(--warn)" }}>
        {eventInfo ? "● connected" : "connecting…"}
      </p>
      <button
        type="button"
        disabled={starting}
        onClick={() => void handleStart()}
        className="py-4 px-10 rounded-[var(--radius-pill)] font-medium text-lg disabled:opacity-50"
        style={{ background: "var(--accent)", color: "var(--bg-0)" }}
      >
        Start show
      </button>
      <p className="text-xs mt-6 max-w-sm" style={{ color: "var(--ink-muted)" }}>
        Unlocks sound for reel premieres and keeps this screen awake.
      </p>
    </div>
  );
}
