"use client";

import type { WrapReport } from "@/lib/hostTypes";

/** The wrap-up report (spec 08 §2 step 3) — honest gaps included, never hidden. Every number here
 * is what `backend/api/host.py::finalize_event` already computed from real aggregates; this
 * component only lays it out. */
export function WrapReportPanel({ report }: { report: WrapReport }) {
  return (
    <section className="mb-8">
      <p className="font-[var(--font-display)] text-lg mb-3" style={{ color: "var(--ivory)" }}>
        Wrap-up report
      </p>
      <div
        className="rounded-[var(--radius-card)] p-4 mb-4"
        style={{ background: "var(--bg-1)", border: "var(--hairline)" }}
      >
        <p className="font-[var(--font-display)] text-xl mb-1" style={{ color: "var(--gold-300)" }}>
          {report.headline}
        </p>
        <p className="text-sm font-mono tabular-nums" style={{ color: "var(--ink-muted)" }}>
          {report.totalPhotos} photos · {report.totalReels} reels · {report.totalPhotographers} photographers
        </p>
      </div>

      {report.perStage.length > 0 && (
        <div className="space-y-2 mb-4">
          {report.perStage.map((row) => (
            <div key={row.stageId} className="flex items-center justify-between text-sm">
              <span style={{ color: "var(--ivory)" }}>{row.label}</span>
              <span className="font-mono tabular-nums" style={{ color: "var(--ink-muted)" }}>
                {row.photoCount} photos · {row.highlightCount} highlights · avg {row.meanAesthetic.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}

      {report.honestGaps.length > 0 && (
        <div className="mb-4">
          <p className="text-sm mb-2" style={{ color: "var(--warn)" }}>
            Gaps this event never filled:
          </p>
          <ul className="text-sm space-y-1">
            {report.honestGaps.map((g) => (
              <li key={`${g.stageId}-${g.momentId}`} style={{ color: "var(--ink-muted)" }}>
                • no photos of {g.momentLabel} during {g.stageLabel}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.topContributors.length > 0 && (
        <div>
          <p className="text-sm mb-2" style={{ color: "var(--ink-muted)" }}>
            Top contributors
          </p>
          <div className="space-y-1">
            {report.topContributors.map((c, i) => (
              <div key={c.uid} className="flex items-center gap-3 text-sm">
                <span className="font-mono w-5 text-right" style={{ color: "var(--ink-muted)" }}>
                  {i + 1}
                </span>
                <span className="flex-1" style={{ color: "var(--ivory)" }}>
                  {c.displayName ?? "Mystery guest 🎭"}
                </span>
                <span className="font-mono tabular-nums" style={{ color: "var(--accent)" }}>
                  {c.points}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
