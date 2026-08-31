"use client";

import { useEffect, useState } from "react";
import { Target, Camera, X } from "lucide-react";
import type { BountyDoc } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";

export function MissionsSheet({
  eventId,
  onShootNow,
  onClose,
}: {
  eventId: string;
  onShootNow: (bountyId: string) => void;
  onClose: () => void;
}) {
  const [bounties, setBounties] = useState<BountyDoc[]>([]);

  useEffect(
    () => listenActiveBounties(eventId, setBounties, () => setBounties([])),
    [eventId]
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-3xl p-6 pb-8 max-h-[75vh] overflow-y-auto scroll-slim glass-card border-t border-[var(--hairline-accent)] shadow-2xl"
        style={{ background: "rgba(23, 16, 20, 0.96)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4 border-b border-white/10 mb-5">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
              <Target className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
                Active Photo Missions
              </h3>
              <p className="text-[11px] text-[var(--ink-muted)]">
                Directives requested by the Story Director
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

        {bounties.length === 0 ? (
          <div className="text-center py-10 text-[var(--ink-muted)]">
            <Target className="w-10 h-10 mx-auto mb-2 opacity-30 text-[var(--gold-500)]" />
            <p className="text-sm">No open missions at this moment.</p>
            <p className="text-xs mt-1">The director issues new bounties as the event progresses.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {bounties.map((b) => (
              <div
                key={b.bountyId}
                className="rounded-2xl p-4 glass-card border border-white/10 hover:border-[var(--accent)] transition-all flex flex-col gap-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <h4 className="font-[family-name:var(--font-display)] text-base font-medium text-[var(--ivory)] leading-snug">
                    {b.copy || b.title}
                  </h4>
                  <span className="px-2.5 py-1 rounded-full bg-[var(--gold-500)] text-black font-mono text-xs font-bold shrink-0">
                    +{b.points} pts
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    onShootNow(b.bountyId);
                    onClose();
                  }}
                  className="btn-primary py-2 px-4 text-xs font-semibold flex items-center justify-center gap-1.5 w-full"
                >
                  <Camera className="w-3.5 h-3.5" />
                  <span>Fulfill this mission</span>
                </button>
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-6 py-3 rounded-full btn-secondary text-sm font-medium"
        >
          Close Missions
        </button>
      </div>
    </div>
  );
}
