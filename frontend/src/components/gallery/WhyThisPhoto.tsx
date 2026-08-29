"use client";

import { CheckCircle, ShieldCheck, X } from "lucide-react";
import type { SlotFactors } from "@/lib/types";

export function WhyThisPhoto({
  factors,
  onClose,
}: {
  factors: SlotFactors;
  onClose: () => void;
}) {
  const rows: Array<[string, string]> = [
    ["Aesthetic Quality", factors.aesthetic.toFixed(2)],
    ["VIP / Subject Weight", `${factors.vipWeight.toFixed(1)}×`],
    ["Freshness / Recency", factors.recency.toFixed(2)],
    ["Timeline Stage Match", factors.stageMatch.toFixed(2)],
  ];

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
            <span className="text-[var(--gold-300)] font-sans font-semibold text-xs">Aesthetic Rank</span>
            <span className="text-[var(--accent)] font-bold text-sm">#{factors.rank}</span>
          </div>
        </div>

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
