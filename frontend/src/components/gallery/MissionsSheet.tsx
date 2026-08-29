"use client";

import { useEffect, useState } from "react";
import type { BountyDoc } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";

/** The Missions chip's bottom sheet (spec 12 §5.2 point 3): every active/escalated bounty plus
 * a "Shoot now" per mission — the non-intrusive counterpart to `BountyBanner`'s interrupt. */
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
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-[var(--radius-banner)] p-5 pb-8 max-h-[70vh] overflow-y-auto"
        style={{ background: "var(--bg-1)", border: "var(--hairline)", borderBottom: "none" }}
        onClick={(e) => e.stopPropagation()}
      >
        <p className="font-[family-name:var(--font-display)] text-xl mb-4" style={{ color: "var(--ivory)" }}>
          Missions
        </p>

        {bounties.length === 0 ? (
          <p style={{ color: "var(--ink-muted)" }}>
            The director is watching the coverage. Missions will appear here.
          </p>
        ) : (
          <div className="space-y-3">
            {bounties.map((b) => (
              <div
                key={b.bountyId}
                className="rounded-[var(--radius-card)] p-4"
                style={{ border: "var(--hairline)" }}
              >
                <div className="flex items-center justify-between gap-3 mb-2">
                  <p className="font-[family-name:var(--font-display)] text-lg" style={{ color: "var(--ivory)" }}>
                    {b.copy || b.title}
                  </p>
                  <span
                    className="px-2 py-1 rounded-[var(--radius-pill)] font-mono text-xs shrink-0"
                    style={{ background: "var(--gold-500)", color: "var(--bg-0)" }}
                  >
                    +{b.points}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    onShootNow(b.bountyId);
                    onClose();
                  }}
                  className="text-sm px-4 py-1.5 rounded-[var(--radius-pill)] font-medium"
                  style={{ background: "var(--accent)", color: "var(--bg-0)" }}
                >
                  Shoot now
                </button>
              </div>
            ))}
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
