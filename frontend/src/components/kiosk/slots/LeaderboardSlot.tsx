"use client";

import { useEffect, useState } from "react";
import { Trophy, User } from "lucide-react";
import type { LeaderboardEntry, LeaderboardSlot as LeaderboardSlotType } from "@/lib/types";
import { listenLeaderboard, listenPeopleDirectory } from "@/lib/firestore";

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
    <div className="absolute inset-0 flex flex-col items-center justify-center px-6" style={{ background: "var(--bg-0)" }}>
      <div className="flex items-center gap-2 mb-8">
        <Trophy className="w-5 h-5 text-[var(--accent)]" />
        <p className="font-mono text-xs tracking-[0.25em] font-semibold text-[var(--accent)] uppercase">
          EVENT LEADERBOARD
        </p>
      </div>

      <div className="w-full max-w-xl space-y-3.5">
        {entries.length === 0 ? (
          <p className="text-center text-sm text-[var(--ink-muted)]">
            The Story Director is actively evaluating incoming submissions.
          </p>
        ) : (
          entries.map((entry, i) => {
            const name = entry.personId ? names[entry.personId] : null;
            return (
              <div
                key={entry.uid}
                className="flex items-center gap-5 p-3.5 rounded-2xl glass-card border border-white/10 shadow-lg"
              >
                <span className="font-mono text-base font-bold w-8 text-center text-[var(--gold-300)]">
                  {i + 1}
                </span>
                <div className="flex-1 flex items-center gap-2.5 min-w-0">
                  <div className="p-1.5 rounded-lg bg-white/5 text-[var(--ink-muted)]">
                    <User className="w-4 h-4" />
                  </div>
                  <span className="font-[family-name:var(--font-display)] text-2xl font-medium text-[var(--ivory)] truncate">
                    {name ?? "Guest Contributor"}
                  </span>
                </div>
                <span className="font-mono tabular-nums text-2xl font-bold text-[var(--accent)]">
                  {entry.points} pts
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
