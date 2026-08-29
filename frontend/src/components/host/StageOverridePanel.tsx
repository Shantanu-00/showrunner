"use client";

import { useState } from "react";
import { Play, RotateCcw, Check } from "lucide-react";
import { setStageOverride } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { HostEventDoc } from "@/lib/hostTypes";

export function StageOverridePanel({
  event,
  eventId,
  onChanged,
}: {
  event: HostEventDoc;
  eventId: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (event.stages.length === 0 || event.status === "wrapped") return null;
  const current = event.stageOverride ?? event.activeStage ?? null;

  async function pick(stageId: string | null) {
    setBusy(stageId ?? "clear");
    setError(null);
    try {
      await setStageOverride(eventId, stageId);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-center gap-2 mb-2">
        <Play className="w-4 h-4 text-[var(--accent)]" />
        <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
          Live Stage Override
        </h3>
      </div>
      <p className="text-xs text-[var(--ink-muted)] mb-4 leading-relaxed">
        Instantly force the active stage context across kiosk wall and story director algorithms.
      </p>

      <div className="flex flex-wrap gap-2.5">
        {event.stages.map((s) => {
          const isActive = current === s.stageId;
          return (
            <button
              key={s.stageId}
              type="button"
              onClick={() => void pick(s.stageId)}
              disabled={busy !== null}
              className={`flex items-center gap-1.5 text-xs px-4 py-2.5 rounded-full font-semibold transition-all ${
                isActive
                  ? "bg-[var(--accent)] text-black shadow-md scale-105"
                  : "bg-white/5 border border-white/10 text-[var(--ivory)] hover:border-white/20"
              }`}
            >
              {isActive && <Check className="w-3.5 h-3.5 stroke-[3]" />}
              <span>{busy === s.stageId ? "Switching…" : s.label}</span>
            </button>
          );
        })}

        {event.stageOverride && (
          <button
            type="button"
            onClick={() => void pick(null)}
            disabled={busy !== null}
            className="flex items-center gap-1.5 text-xs px-4 py-2.5 rounded-full bg-white/5 border border-white/10 text-[var(--ink-muted)] hover:text-white transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>{busy === "clear" ? "Clearing…" : "Restore Auto Schedule"}</span>
          </button>
        )}
      </div>

      {error && (
        <p className="text-xs mt-3 text-[var(--danger)]">
          {error}
        </p>
      )}
    </section>
  );
}
