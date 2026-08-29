"use client";

import { useEffect, useState } from "react";
import { parseItinerary, saveStages } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { EventStageDoc, HostEventDoc, RequiredMoment } from "@/lib/hostTypes";

interface DraftStage {
  key: string;
  stageId: string;
  label: string;
  timeHint?: string;
  startsAt: string; // <input type="datetime-local"> value, local time
  endsAt: string;
  requiredMoments: RequiredMoment[];
}

function slugify(label: string): string {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "stage";
}

function fromEventStage(s: EventStageDoc, i: number): DraftStage {
  return {
    key: `${s.stageId}-${i}`,
    stageId: s.stageId,
    label: s.label,
    startsAt: s.startsAt ? s.startsAt.slice(0, 16) : "",
    endsAt: s.endsAt ? s.endsAt.slice(0, 16) : "",
    requiredMoments: s.requiredMoments ?? [],
  };
}

/** Model Armor sanitize → Gemini structured parse → an editable, host-reviewed table (spec 08
 * §3.2). "An LLM parse of a WhatsApp itinerary forward is never silently authoritative" — the
 * model gives structure and a `timeHint`; only the host's own date/time pickers below produce
 * the real `startsAt`/`endsAt` the stage-fusion temporal prior (spec 03 §5.1) actually reads. */
export function ItineraryPanel({ event, eventId }: { event: HostEventDoc; eventId: string }) {
  const [raw, setRaw] = useState("");
  const [stages, setStages] = useState<DraftStage[]>(
    event.stages.map((s, i) => fromEventStage(s, i))
  );
  const [warnings, setWarnings] = useState<string[]>([]);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    setStages(event.stages.map((s, i) => fromEventStage(s, i)));
  }, [event.eventId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleParse() {
    if (!raw.trim()) return;
    setParsing(true);
    setError(null);
    try {
      const out = await parseItinerary(eventId, raw);
      setStages(
        out.stages.map((s, i) => ({
          key: `${s.stageId}-${i}-${Date.now()}`,
          stageId: s.stageId,
          label: s.label,
          timeHint: s.timeHint,
          startsAt: "",
          endsAt: "",
          requiredMoments: s.requiredMoments,
        }))
      );
      setWarnings(out.warnings);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "Couldn't parse that paste — you can still add stages manually below."
          : "Something went wrong."
      );
    } finally {
      setParsing(false);
    }
  }

  function addStage() {
    setStages((prev) => [
      ...prev,
      { key: `new-${Date.now()}`, stageId: "", label: "New stage", startsAt: "", endsAt: "", requiredMoments: [] },
    ]);
  }

  function removeStage(key: string) {
    setStages((prev) => prev.filter((s) => s.key !== key));
  }

  function updateStage(key: string, patch: Partial<DraftStage>) {
    setStages((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  }

  function addMoment(key: string, label: string) {
    if (!label.trim()) return;
    updateStage(key, {
      requiredMoments: [
        ...(stages.find((s) => s.key === key)?.requiredMoments ?? []),
        { momentId: slugify(label), label: label.trim() },
      ],
    });
  }

  function removeMoment(key: string, momentId: string) {
    const stage = stages.find((s) => s.key === key);
    if (!stage) return;
    updateStage(key, { requiredMoments: stage.requiredMoments.filter((m) => m.momentId !== momentId) });
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload = stages.map((s) => ({
        stageId: s.stageId || slugify(s.label),
        label: s.label,
        startsAt: s.startsAt ? new Date(s.startsAt).toISOString() : null,
        endsAt: s.endsAt ? new Date(s.endsAt).toISOString() : null,
        requiredMoments: s.requiredMoments,
        theme: null,
      }));
      await saveStages(eventId, payload);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? `Couldn't save (${err.status}).` : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  const canEdit = event.status === "draft" || event.status === "live" || event.status === "paused";

  return (
    <section className="mb-8">
      <p className="font-[family-name:var(--font-display)] text-lg mb-3" style={{ color: "var(--ivory)" }}>
        Timeline
      </p>

      {canEdit && (
        <>
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder="Paste the itinerary — WhatsApp forward, PDF text, anything…"
            rows={4}
            className="w-full mb-2 px-4 py-3 rounded-[var(--radius-card)] text-sm"
            style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
          />
          <button
            type="button"
            onClick={() => void handleParse()}
            disabled={parsing || !raw.trim()}
            className="text-sm px-4 py-2 rounded-[var(--radius-pill)] mb-4"
            style={{ border: "var(--hairline)", color: "var(--accent)", opacity: parsing ? 0.6 : 1 }}
          >
            {parsing ? "The Curator is reading it…" : "Parse itinerary"}
          </button>
        </>
      )}

      {warnings.length > 0 && (
        <div className="mb-4 text-sm" style={{ color: "var(--warn)" }}>
          {warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </div>
      )}

      <div className="space-y-3 mb-4">
        {stages.map((stage) => (
          <div key={stage.key} className="rounded-[var(--radius-card)] p-4" style={{ border: "var(--hairline)" }}>
            <div className="flex items-center gap-2 mb-2">
              <input
                value={stage.label}
                disabled={!canEdit}
                onChange={(e) => updateStage(stage.key, { label: e.target.value })}
                className="flex-1 font-[family-name:var(--font-display)] text-lg bg-transparent"
                style={{ color: "var(--ivory)" }}
              />
              {stage.timeHint && (
                <span className="text-xs shrink-0" style={{ color: "var(--ink-muted)" }}>
                  as written: {stage.timeHint}
                </span>
              )}
              {canEdit && (
                <button type="button" onClick={() => removeStage(stage.key)} style={{ color: "var(--ink-muted)" }}>
                  ✕
                </button>
              )}
            </div>

            <div className="flex gap-3 mb-3">
              <label className="flex-1 text-xs" style={{ color: "var(--ink-muted)" }}>
                Starts
                <input
                  type="datetime-local"
                  value={stage.startsAt}
                  disabled={!canEdit}
                  onChange={(e) => updateStage(stage.key, { startsAt: e.target.value })}
                  className="block w-full mt-1 px-2 py-1.5 rounded-[var(--radius-card)]"
                  style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
                />
              </label>
              <label className="flex-1 text-xs" style={{ color: "var(--ink-muted)" }}>
                Ends
                <input
                  type="datetime-local"
                  value={stage.endsAt}
                  disabled={!canEdit}
                  onChange={(e) => updateStage(stage.key, { endsAt: e.target.value })}
                  className="block w-full mt-1 px-2 py-1.5 rounded-[var(--radius-card)]"
                  style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
                />
              </label>
            </div>

            <div className="flex flex-wrap gap-2 mb-2">
              {stage.requiredMoments.map((m) => (
                <span
                  key={m.momentId}
                  className="text-xs px-2 py-1 rounded-[var(--radius-pill)] flex items-center gap-1"
                  style={{ background: "var(--bg-1)", color: "var(--ink-muted)" }}
                >
                  {m.label}
                  {canEdit && (
                    <button type="button" onClick={() => removeMoment(stage.key, m.momentId)}>
                      ✕
                    </button>
                  )}
                </span>
              ))}
            </div>
            {canEdit && (
              <MomentAdder onAdd={(label) => addMoment(stage.key, label)} />
            )}
          </div>
        ))}
      </div>

      {canEdit && (
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={addStage}
            className="text-sm px-4 py-2 rounded-[var(--radius-pill)]"
            style={{ border: "var(--hairline)", color: "var(--ink-muted)" }}
          >
            + Add stage
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || stages.length === 0}
            className="text-sm px-5 py-2 rounded-[var(--radius-pill)] font-medium"
            style={{ background: "var(--accent)", color: "var(--bg-0)", opacity: saving ? 0.6 : 1 }}
          >
            {saving ? "Saving…" : "Save stages"}
          </button>
          {savedAt && (
            <span className="text-xs" style={{ color: "var(--ok)" }}>
              Saved
            </span>
          )}
        </div>
      )}

      {error && (
        <p className="text-sm mt-3" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </section>
  );
}

function MomentAdder({ onAdd }: { onAdd: (label: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex gap-2">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            onAdd(value);
            setValue("");
          }
        }}
        placeholder="+ required moment"
        className="text-xs px-3 py-1.5 rounded-[var(--radius-pill)] flex-1"
        style={{ background: "var(--bg-0)", border: "var(--hairline)", color: "var(--ivory)" }}
      />
      <button
        type="button"
        onClick={() => {
          onAdd(value);
          setValue("");
        }}
        className="text-xs px-3 py-1.5 rounded-[var(--radius-pill)]"
        style={{ border: "var(--hairline)", color: "var(--ink-muted)" }}
      >
        Add
      </button>
    </div>
  );
}
