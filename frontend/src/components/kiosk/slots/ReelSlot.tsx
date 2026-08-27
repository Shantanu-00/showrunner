"use client";

import { useEffect, useState } from "react";
import type { ReelDoc, ReelSlot as ReelSlotType } from "@/lib/types";
import { listenReel } from "@/lib/firestore";

/** `reel` premiere takeover storyboard (spec 12 §6): title card → play → end card. The render
 * pipeline is a later session (spec 06); this renders whatever's actually there today —
 * a real published clip if one exists, honestly "still in the edit room" if not. */
export function ReelSlot({ eventId, slot }: { eventId: string; slot: ReelSlotType }) {
  const [reel, setReel] = useState<ReelDoc | null>(null);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    setEnded(false);
    return listenReel(eventId, slot.reelId, setReel, () => setReel(null));
  }, [eventId, slot.reelId]);

  if (!reel?.videoUri || ended) {
    return (
      <div
        className="absolute inset-0 flex flex-col items-center justify-center text-center px-[10%]"
        style={{ background: "var(--bg-0)" }}
      >
        {slot.premiere && (
          <p className="font-mono text-xs tracking-[0.25em] mb-4" style={{ color: "var(--accent)" }}>
            TONIGHT&rsquo;S PREMIERE
          </p>
        )}
        <p
          className="font-[var(--font-display)] mb-4"
          style={{ color: "var(--ivory)", fontSize: "min(9vw, 61px)" }}
        >
          {reel?.title ?? "The Couple"}
        </p>
        {!reel?.videoUri && (
          <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
            Tonight&rsquo;s premieres are still in the edit room.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col" style={{ background: "var(--bg-0)" }}>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <video
        src={reel.videoUri}
        autoPlay
        playsInline
        className="w-full h-full object-contain"
        onEnded={() => setEnded(true)}
      />
      <div
        className="absolute inset-x-0 bottom-[6%] text-center font-[var(--font-display)] italic"
        style={{ color: "var(--ivory)" }}
      >
        Directed by Showrunner · soundtrack composed by Lyria
      </div>
    </div>
  );
}
