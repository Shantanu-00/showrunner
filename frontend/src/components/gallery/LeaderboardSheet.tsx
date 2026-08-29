"use client";

import { useEffect, useState } from "react";
import { Trophy, User, Medal, X } from "lucide-react";
import type { LeaderboardEntry } from "@/lib/types";
import { listenLeaderboard, listenPeopleDirectory } from "@/lib/firestore";

const TOP_N = 10;

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
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-3xl p-6 pb-8 max-h-[75vh] overflow-y-auto glass-card border-t border-[var(--hairline-accent)] shadow-2xl"
        style={{ background: "rgba(23, 16, 20, 0.96)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4 border-b border-white/10 mb-5">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
              <Trophy className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
                Event Leaderboard
              </h3>
              <p className="text-[11px] text-[var(--ink-muted)]">
                Top photo bounty contributors
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {entries.length === 0 ? (
          <div className="text-center py-10 text-[var(--ink-muted)]">
            <Medal className="w-10 h-10 mx-auto mb-2 opacity-40 text-[var(--gold-500)]" />
            <p className="text-sm">Nobody is on the board yet.</p>
            <p className="text-xs mt-1">Complete photo missions to claim the top spot!</p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {entries.map((entry, i) => {
              const name = entry.personId ? names[entry.personId] : null;
              const isTop3 = i < 3;
              return (
                <div
                  key={entry.uid}
                  className={`flex items-center gap-3.5 p-3 rounded-2xl transition-all ${
                    isTop3
                      ? "bg-white/5 border border-[var(--gold-500)]/20 shadow-sm"
                      : "bg-transparent border border-white/5"
                  }`}
                >
                  <div
                    className={`w-7 h-7 rounded-full flex items-center justify-center font-mono text-xs font-bold shrink-0 ${
                      i === 0
                        ? "bg-[var(--gold-500)] text-black"
                        : i === 1
                        ? "bg-slate-300 text-black"
                        : i === 2
                        ? "bg-amber-700 text-white"
                        : "text-[var(--ink-muted)] bg-white/5"
                    }`}
                  >
                    {i + 1}
                  </div>

                  <div className="flex-1 min-w-0 flex items-center gap-2">
                    <div className="p-1 rounded-md bg-white/5 text-[var(--ink-muted)]">
                      <User className="w-3.5 h-3.5" />
                    </div>
                    <span className="font-[family-name:var(--font-display)] text-base font-medium truncate text-[var(--ivory)]">
                      {name ?? "Guest Contributor"}
                    </span>
                  </div>

                  <div className="flex items-center gap-1 font-mono text-sm font-semibold tabular-nums text-[var(--accent)]">
                    <span>{entry.points}</span>
                    <span className="text-[10px] text-[var(--ink-muted)] font-normal uppercase">pts</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-6 py-3 rounded-full btn-secondary text-sm font-medium"
        >
          Close Leaderboard
        </button>
      </div>
    </div>
  );
}
