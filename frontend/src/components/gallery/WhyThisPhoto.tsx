"use client";

import type { SlotFactors } from "@/lib/types";

/** The glass-box ranking card (spec 04 §4, spec 12 §8) — renders only stored/derived score
 * factors, zero new computation, zero LLM. Host/judge mode only; never shown to a regular guest. */
export function WhyThisPhoto({
  factors,
  onClose,
}: {
  factors: SlotFactors;
  onClose: () => void;
}) {
  const rows: Array<[string, string]> = [
    ["aesthetic", factors.aesthetic.toFixed(2)],
    ["vip", `${factors.vipWeight.toFixed(1)}×`],
    ["freshness", factors.recency.toFixed(2)],
    ["stage", factors.stageMatch.toFixed(2)],
  ];

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center px-6"
      style={{ background: "rgba(0,0,0,0.7)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-[var(--radius-card)] p-5"
        style={{ background: "var(--bg-glass)", border: "var(--hairline)", backdropFilter: "blur(16px)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <p className="font-mono text-xs mb-3" style={{ color: "var(--accent)" }}>
          WHY THIS PHOTO
        </p>
        <div className="space-y-1 font-mono text-sm tabular-nums" style={{ color: "var(--ivory)" }}>
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between">
              <span style={{ color: "var(--ink-muted)" }}>{label}</span>
              <span>{value}</span>
            </div>
          ))}
          <div className="flex justify-between pt-2" style={{ borderTop: "var(--hairline)" }}>
            <span style={{ color: "var(--ink-muted)" }}>rank</span>
            <span>#{factors.rank}</span>
          </div>
        </div>
        <p className="text-xs mt-4" style={{ color: "var(--ink-muted)" }}>
          consent ✓ · Guardian ✓ — numbers the director already computed, not a new judgment.
        </p>
      </div>
    </div>
  );
}
