"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Sparkles } from "lucide-react";
import { listenGuestPoints } from "@/lib/firestore";

const HOLD_MS = 1500;
const PARTICLE_COUNT = 28;
const COLORS = ["var(--gold-500)", "var(--gold-300)", "var(--maroon-500)", "#ffffff"];

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
    <div className="fixed inset-x-0 top-24 z-[70] flex justify-center pointer-events-none">
      <div className="relative flex items-center justify-center">
        <div
          className="points-pop px-6 py-3 rounded-full font-mono text-2xl font-bold tabular-nums shadow-2xl flex items-center gap-2 border-2 border-white/20"
          style={{
            background: "linear-gradient(135deg, var(--gold-400) 0%, var(--gold-500) 100%)",
            color: "#0b0709",
            boxShadow: "0 0 40px rgba(212, 175, 106, 0.6)",
          }}
        >
          <Sparkles className="w-6 h-6 animate-spin" style={{ animationDuration: "3s" }} />
          <span>+{delta} pts</span>
        </div>
        {Array.from({ length: PARTICLE_COUNT }, (_, i) => {
          const angle = (i / PARTICLE_COUNT) * 360;
          const dist = 65 + (i % 3) * 26;
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
              className="confetti-piece absolute left-1/2 top-1/2 w-2.5 h-2.5 rounded-sm shadow-md"
              style={style}
            />
          );
        })}
      </div>
    </div>
  );
}
