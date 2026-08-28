"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { listenGuestPoints } from "@/lib/firestore";

const HOLD_MS = 1400;
const PARTICLE_COUNT = 24;
const COLORS = ["var(--gold-500)", "var(--gold-300)", "var(--maroon-500)"];

/** The award half of the bounty banner's choreography (spec 12 §7: "check-morph → points
 * count-up → themed confetti burst"), deliberately decoupled from `BountyBanner`: the signal is
 * `guests/{uid}.points` increasing, not a specific bounty's `submissions` array, so this also
 * fires correctly for a partial-credit award or any future point source without the client
 * having to reconstruct which mission earned it (spec 05 §3 — `points` is already the ledger's
 * running total, scaled and clamped server-side). Mounted once per session, always on. */
export function AwardBurst({ eventId, uid }: { eventId: string; uid: string }) {
  const [delta, setDelta] = useState<number | null>(null);
  const prevRef = useRef<number | null>(null);

  useEffect(() => {
    prevRef.current = null;
    return listenGuestPoints(eventId, uid, (points) => {
      if (prevRef.current != null && points > prevRef.current) {
        setDelta(points - prevRef.current);
        if (typeof navigator !== "undefined" && navigator.vibrate) navigator.vibrate(40);
        window.setTimeout(() => setDelta(null), HOLD_MS);
      }
      prevRef.current = points;
    });
  }, [eventId, uid]);

  if (delta == null) return null;

  return (
    <div className="fixed inset-x-0 top-28 z-[70] flex justify-center pointer-events-none">
      <div className="relative">
        <div
          className="points-pop px-6 py-3 rounded-[var(--radius-pill)] font-mono text-2xl tabular-nums"
          style={{ background: "var(--gold-500)", color: "var(--bg-0)" }}
        >
          +{delta}
        </div>
        {Array.from({ length: PARTICLE_COUNT }, (_, i) => {
          const angle = (i / PARTICLE_COUNT) * 360;
          const dist = 60 + (i % 3) * 24;
          const x = Math.cos((angle * Math.PI) / 180) * dist;
          const style: CSSProperties & Record<"--confetti-x" | "--confetti-rot", string> = {
            background: COLORS[i % COLORS.length],
            animationDelay: `${(i % 5) * 30}ms`,
            "--confetti-x": `${x}px`,
            "--confetti-rot": `${(i * 47) % 360}deg`,
          };
          return (
            <span
              key={i}
              className="confetti-piece absolute left-1/2 top-1/2 w-2 h-2 rounded-sm"
              style={style}
            />
          );
        })}
      </div>
    </div>
  );
}
