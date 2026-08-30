"use client";

import { CheckCircle, ShieldCheck, X } from "lucide-react";
import type { SlotFactors } from "@/lib/types";

/**
 * The glass-box ranking card (spec 04 §4 / spec 12 §8).
 *
 * The rule this component exists to keep: **every number on it is one the system actually computed.**
 * On the kiosk the factors are read off the slot the publisher stored, so the card cannot disagree
 * with the decision. On the gallery they are derived client-side from the same public fields by the
 * same formula (`lib/scoring.ts`) — a real computation of real data. Anything the surface genuinely
 * cannot know is *omitted*, never defaulted: a row reading `1.00` because there was nothing to put
 * there is the one failure mode a transparency card must not have.
 *
 * `rankLabel` is required rather than defaulted because "rank" means different things on different
 * surfaces — score order on the kiosk and in Director's Picks, capture-time order on the Latest tab —
 * and a card that calls a recency position an "Aesthetic Rank" is lying in the same way a fabricated
 * number would.
 */
export function WhyThisPhoto({
  factors,
  rankLabel,
  rankNote,
  onClose,
}: {
  factors: SlotFactors;
  /** What ordering `factors.rank` is a position within. */
  rankLabel: string;
  /** One clause naming what produced the ordering, shown under the rank chip. */
  rankNote?: string;
  onClose: () => void;
}) {
  const rows: Array<[string, string]> = [
    ["Aesthetic Quality", factors.aesthetic.toFixed(2)],
    ["VIP / Subject Weight", `${factors.vipWeight.toFixed(1)}×`],
    ["Freshness / Recency", factors.recency.toFixed(2)],
    ["Timeline Stage Match", factors.stageMatch.toFixed(2)],
  ];
  if (factors.diversity !== undefined) {
    rows.push(["Diversity Multiplier", factors.diversity.toFixed(2)]);
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center px-4 bg-black/80 backdrop-blur-md animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-3xl p-6 glass-card border border-[var(--hairline-accent)] shadow-2xl"
        style={{ background: "rgba(23, 16, 20, 0.96)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-3 border-b border-white/10 mb-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] font-semibold text-[var(--accent)]">
              TRANSPARENT AI SCORING
            </p>
            <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
              Why This Photo?
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-2.5 font-mono text-xs tabular-nums">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between items-center p-2 rounded-xl bg-white/5">
              <span className="text-[var(--ink-muted)]">{label}</span>
              <span className="font-semibold text-[var(--ivory)]">{value}</span>
            </div>
          ))}

          <div className="flex justify-between items-center p-2.5 rounded-xl bg-[var(--gold-500)]/15 border border-[var(--gold-500)]/30 mt-2">
            <span className="text-[var(--gold-300)] font-sans font-semibold text-xs">{rankLabel}</span>
            <span className="text-[var(--accent)] font-bold text-sm">#{factors.rank + 1}</span>
          </div>
        </div>

        {rankNote && (
          <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed mt-2.5">{rankNote}</p>
        )}

        {factors.diversity === undefined && (
          <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed mt-2.5">
            The diversity multiplier only exists on the kiosk wall, where a slot can be forced to
            repeat a face &mdash; so it isn&rsquo;t shown here.
          </p>
        )}

        <div className="flex items-center gap-3 mt-4 pt-3 border-t border-white/10 text-[11px] text-[var(--ink-muted)]">
          <div className="flex items-center gap-1 text-[var(--ok)]">
            <CheckCircle className="w-3.5 h-3.5" />
            <span>Consent OK</span>
          </div>
          <div className="flex items-center gap-1 text-[var(--ok)]">
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>Guardian Passed</span>
          </div>
        </div>
      </div>
    </div>
  );
}
