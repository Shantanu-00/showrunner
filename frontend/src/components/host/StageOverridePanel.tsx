"use client";

import { useState } from "react";
import { setStageOverride } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { HostEventDoc } from "@/lib/hostTypes";

/** "Now: ▶ stage" — always wins over the schedule/evidence fusion, instantly (spec 05 §2). Only
 * shown once the event has stages to override between; disabled once wrapped. */
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
    <section className="mb-8">
      <p className="font-[var(--font-display)] text-lg mb-3" style={{ color: "var(--ivory)" }}>
        Now: ▶ stage
      </p>
      <div className="flex flex-wrap gap-2">
        {event.stages.map((s) => (
          <button
            key={s.stageId}
            type="button"
            onClick={() => void pick(s.stageId)}
            disabled={busy !== null}
            className="text-sm px-4 py-2 rounded-[var(--radius-pill)]"
            style={
              current === s.stageId
                ? { background: "var(--accent)", color: "var(--bg-0)" }
                : { border: "var(--hairline)", color: "var(--ivory)" }
            }
          >
            {busy === s.stageId ? "…" : s.label}
          </button>
        ))}
        {event.stageOverride && (
          <button
            type="button"
            onClick={() => void pick(null)}
            disabled={busy !== null}
            className="text-sm px-4 py-2 rounded-[var(--radius-pill)]"
            style={{ border: "var(--hairline)", color: "var(--ink-muted)" }}
          >
            {busy === "clear" ? "…" : "Clear override"}
          </button>
        )}
      </div>
      {error && (
        <p className="text-sm mt-2" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </section>
  );
}
