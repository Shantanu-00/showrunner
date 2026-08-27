"use client";

import { useEffect, useState } from "react";
import type { LeaderboardEntry, LeaderboardSlot as LeaderboardSlotType } from "@/lib/types";
import { listenLeaderboard, listenPeopleDirectory } from "@/lib/firestore";

/** `leaderboard` — top-N, end-credits styling (spec 12 §6). Un-enrolled uids are never hidden,
 * only unnamed (spec 12 §5.2's "Mystery guest" rule — points are never lost). */
export function LeaderboardSlot({ eventId, slot }: { eventId: string; slot: LeaderboardSlotType }) {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [names, setNames] = useState<Record<string, string | null>>({});

  useEffect(() => {
    return listenLeaderboard(eventId, slot.topN, setEntries, () => setEntries([]));
  }, [eventId, slot.topN]);

  useEffect(() => {
    return listenPeopleDirectory(eventId, setNames, () => setNames({}));
  }, [eventId]);

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center" style={{ background: "var(--bg-0)" }}>
      <p className="font-mono text-xs mb-8 tracking-[0.2em]" style={{ color: "var(--accent)" }}>
        THE LEADERBOARD
      </p>
      <div className="space-y-4">
        {entries.length === 0 ? (
          <p style={{ color: "var(--ink-muted)" }}>The director is watching the coverage.</p>
        ) : (
          entries.map((entry, i) => {
            const name = entry.personId ? names[entry.personId] : null;
            return (
              <div key={entry.uid} className="flex items-center gap-6">
                <span className="font-mono text-lg w-8 text-right" style={{ color: "var(--ink-muted)" }}>
                  {i + 1}
                </span>
                <span className="font-[var(--font-display)] text-2xl" style={{ color: "var(--ivory)" }}>
                  {name ?? "Mystery guest 🎭"}
                </span>
                <span className="font-mono tabular-nums text-xl" style={{ color: "var(--accent)" }}>
                  {entry.points}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
