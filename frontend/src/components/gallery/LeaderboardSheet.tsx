"use client";

import { useEffect, useState } from "react";
import type { LeaderboardEntry } from "@/lib/types";
import { listenLeaderboard, listenPeopleDirectory } from "@/lib/firestore";

const TOP_N = 10;

/** The 🏆 top-bar entry's bottom sheet (spec 12 §5.2 point 3/7). Same identity rule as the
 * kiosk's `LeaderboardSlot`: un-enrolled uids show as "Mystery guest 🎭" — points are never
 * lost, only the name is missing until they enroll. */
export function LeaderboardSheet({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [names, setNames] = useState<Record<string, string | null>>({});

  useEffect(
    () => listenLeaderboard(eventId, TOP_N, setEntries, () => setEntries([])),
    [eventId]
  );
  useEffect(() => listenPeopleDirectory(eventId, setNames, () => setNames({})), [eventId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-[var(--radius-banner)] p-5 pb-8 max-h-[70vh] overflow-y-auto"
        style={{ background: "var(--bg-1)", border: "var(--hairline)", borderBottom: "none" }}
        onClick={(e) => e.stopPropagation()}
      >
        <p className="font-[var(--font-display)] text-xl mb-4" style={{ color: "var(--ivory)" }}>
          🏆 Leaderboard
        </p>

        {entries.length === 0 ? (
          <p style={{ color: "var(--ink-muted)" }}>
            Nobody&rsquo;s on the board yet — be the first to earn points.
          </p>
        ) : (
          <div className="space-y-3">
            {entries.map((entry, i) => {
              const name = entry.personId ? names[entry.personId] : null;
              return (
                <div key={entry.uid} className="flex items-center gap-4">
                  <span className="font-mono w-6 text-right" style={{ color: "var(--ink-muted)" }}>
                    {i + 1}
                  </span>
                  <span
                    className="flex-1 font-[var(--font-display)] text-lg truncate"
                    style={{ color: "var(--ivory)" }}
                  >
                    {name ?? "Mystery guest 🎭"}
                  </span>
                  <span className="font-mono tabular-nums" style={{ color: "var(--accent)" }}>
                    {entry.points}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-6 py-3 rounded-[var(--radius-pill)]"
          style={{ color: "var(--ink-muted)" }}
        >
          Close
        </button>
      </div>
    </div>
  );
}
