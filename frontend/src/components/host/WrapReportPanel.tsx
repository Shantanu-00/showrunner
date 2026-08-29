"use client";

import { Award, FileText, AlertCircle, Trophy, User } from "lucide-react";
import type { WrapReport } from "@/lib/hostTypes";

export function WrapReportPanel({ report }: { report: WrapReport }) {
  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-2xl animate-fadeIn">
      <div className="flex items-center gap-2 mb-4">
        <Award className="w-5 h-5 text-[var(--accent)]" />
        <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
          Event Wrap-Up Synthesis
        </h3>
      </div>

      <div className="rounded-2xl p-5 mb-5 bg-[var(--gold-500)]/10 border border-[var(--gold-500)]/20 shadow-md">
        <h4 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--gold-300)] mb-2">
          {report.headline}
        </h4>
        <div className="flex flex-wrap gap-4 text-xs font-mono text-[var(--ink-muted)]">
          <span>{report.totalPhotos} Ingested Photos</span>
          <span>•</span>
          <span>{report.totalReels} Generated Reels</span>
          <span>•</span>
          <span>{report.totalPhotographers} Photographers</span>
        </div>
      </div>

      {report.perStage.length > 0 && (
        <div className="space-y-2.5 mb-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)] mb-2">
            Coverage Per Stage Phase
          </p>
          {report.perStage.map((row) => (
            <div
              key={row.stageId}
              className="flex items-center justify-between p-3 rounded-xl bg-white/5 border border-white/5 text-xs"
            >
              <span className="font-medium text-[var(--ivory)]">{row.label}</span>
              <span className="font-mono tabular-nums text-[var(--ink-muted)]">
                {row.photoCount} photos · {row.highlightCount} highlights · avg {row.meanAesthetic.toFixed(2)} score
              </span>
            </div>
          ))}
        </div>
      )}

      {report.honestGaps.length > 0 && (
        <div className="mb-6 p-4 rounded-2xl bg-[var(--warn)]/10 border border-[var(--warn)]/20">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--warn)] mb-2">
            <AlertCircle className="w-4 h-4" />
            <span>Honest Unfilled Gaps (Audited Reality)</span>
          </div>
          <ul className="text-xs space-y-1 text-[var(--ink-muted)]">
            {report.honestGaps.map((g) => (
              <li key={`${g.stageId}-${g.momentId}`}>
                • No verified photos captured for &ldquo;{g.momentLabel}&rdquo; during {g.stageLabel}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.topContributors.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)] mb-3 flex items-center gap-1.5">
            <Trophy className="w-4 h-4 text-[var(--accent)]" />
            <span>Top Photo Contributors</span>
          </p>
          <div className="space-y-2">
            {report.topContributors.map((c, i) => (
              <div key={c.uid} className="flex items-center gap-3 p-2.5 rounded-xl bg-white/5 text-xs">
                <span className="font-mono w-5 text-right text-[var(--gold-300)] font-bold">
                  {i + 1}
                </span>
                <div className="flex-1 flex items-center gap-2 text-[var(--ivory)] font-medium">
                  <User className="w-3.5 h-3.5 text-[var(--ink-muted)]" />
                  <span>{c.displayName ?? "Guest Contributor"}</span>
                </div>
                <span className="font-mono font-semibold tabular-nums text-[var(--accent)]">
                  {c.points} pts
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
