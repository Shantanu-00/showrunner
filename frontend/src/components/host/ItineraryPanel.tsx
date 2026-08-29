"use client";

import { useEffect, useState } from "react";
import { Sparkles, Plus, Trash2, Calendar, Clock, AlertTriangle, Check, X, Tag } from "lucide-react";
import { parseItinerary, saveStages } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { EventStageDoc, HostEventDoc, RequiredMoment } from "@/lib/hostTypes";

interface DraftStage {
  key: string;
  stageId: string;
  label: string;
  timeHint?: string;
  startsAt: string;
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
      { key: `new-${Date.now()}`, stageId: "", label: "New Stage Phase", startsAt: "", endsAt: "", requiredMoments: [] },
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
      setError(err instanceof ApiError ? `Couldn't save (${err.status}): ${err.message}` : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  const canEdit = event.status === "draft" || event.status === "live" || event.status === "paused";

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
            Timeline & Stage Windows
          </h3>
          <p className="text-xs text-[var(--ink-muted)]">
            Autonomous timeline graph for EXIF alignment, bounty triggers, and story reel generation.
          </p>
        </div>
      </div>

      {canEdit && (
        <div className="mb-6 p-5 rounded-2xl bg-black/40 border border-white/5 space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--gold-300)]">
            <Sparkles className="w-4 h-4" />
            <span>AI Itinerary Parser (Gemini + Model Armor)</span>
          </div>
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder="Paste raw schedule (WhatsApp forward, invitation timeline, run-of-show text)…"
            rows={3}
            className="w-full px-4 py-3 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void handleParse()}
            disabled={parsing || !raw.trim()}
            className="btn-secondary px-4 py-2 text-xs font-semibold flex items-center gap-1.5 disabled:opacity-40"
          >
            <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
            <span>{parsing ? "Extracting Structured Timeline…" : "Auto-Parse Itinerary"}</span>
          </button>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="mb-4 p-3 rounded-xl bg-[var(--warn)]/15 border border-[var(--warn)]/30 text-xs text-[var(--warn)] space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-4 mb-6">
        {stages.map((stage, idx) => (
          <div key={stage.key} className="rounded-2xl p-5 glass-card bg-black/30 border border-white/10 hover:border-[var(--accent)] transition-all">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-6 h-6 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] flex items-center justify-center font-mono text-xs font-bold">
                {idx + 1}
              </div>
              <input
                value={stage.label}
                disabled={!canEdit}
                onChange={(e) => updateStage(stage.key, { label: e.target.value })}
                className="flex-1 font-[family-name:var(--font-display)] text-lg bg-transparent font-medium text-[var(--ivory)] border-b border-transparent hover:border-white/20 focus:border-[var(--accent)] focus:outline-none"
              />
              {stage.timeHint && (
                <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white/5 text-[var(--ink-muted)] shrink-0">
                  Hint: {stage.timeHint}
                </span>
              )}
              {canEdit && (
                <button
                  type="button"
                  onClick={() => removeStage(stage.key)}
                  className="p-1.5 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-[var(--danger)] transition-colors"
                  aria-label="Remove stage"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
              <label className="text-[11px] text-[var(--ink-muted)] font-medium">
                <span className="flex items-center gap-1 mb-1">
                  <Clock className="w-3 h-3 text-[var(--gold-300)]" />
                  <span>Start Window</span>
                </span>
                <input
                  type="datetime-local"
                  value={stage.startsAt}
                  disabled={!canEdit}
                  onChange={(e) => updateStage(stage.key, { startsAt: e.target.value })}
                  className="block w-full px-3 py-2 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
              <label className="text-[11px] text-[var(--ink-muted)] font-medium">
                <span className="flex items-center gap-1 mb-1">
                  <Clock className="w-3 h-3 text-[var(--gold-300)]" />
                  <span>End Window</span>
                </span>
                <input
                  type="datetime-local"
                  value={stage.endsAt}
                  disabled={!canEdit}
                  onChange={(e) => updateStage(stage.key, { endsAt: e.target.value })}
                  className="block w-full px-3 py-2 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            </div>

            <div className="space-y-2 pt-2 border-t border-white/5">
              <div className="flex items-center gap-1 text-[11px] text-[var(--ink-muted)]">
                <Tag className="w-3 h-3" />
                <span>Required Moments for Story Director:</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {stage.requiredMoments.map((m) => (
                  <span
                    key={m.momentId}
                    className="text-xs px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[var(--ivory)] flex items-center gap-1.5"
                  >
                    <span>{m.label}</span>
                    {canEdit && (
                      <button
                        type="button"
                        onClick={() => removeMoment(stage.key, m.momentId)}
                        className="hover:text-[var(--danger)]"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    )}
                  </span>
                ))}
              </div>
              {canEdit && (
                <MomentAdder onAdd={(label) => addMoment(stage.key, label)} />
              )}
            </div>
          </div>
        ))}
      </div>

      {canEdit && (
        <div className="flex items-center gap-3 pt-3 border-t border-white/10">
          <button
            type="button"
            onClick={addStage}
            className="btn-secondary px-4 py-2.5 text-xs font-semibold flex items-center gap-1.5"
          >
            <Plus className="w-4 h-4" />
            <span>Add Stage Window</span>
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || stages.length === 0}
            className="btn-primary px-6 py-2.5 text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
          >
            {saving ? (
              <span>Committing Graph…</span>
            ) : (
              <>
                <Check className="w-4 h-4" />
                <span>Save Timeline Changes</span>
              </>
            )}
          </button>
          {savedAt && (
            <span className="flex items-center gap-1 text-xs text-[var(--ok)] font-medium">
              <Check className="w-3.5 h-3.5" />
              <span>Saved</span>
            </span>
          )}
        </div>
      )}

      {error && (
        <p className="text-xs mt-3 text-[var(--danger)]">
          {error}
        </p>
      )}
    </section>
  );
}

function MomentAdder({ onAdd }: { onAdd: (label: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex gap-2 pt-1">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            onAdd(value);
            setValue("");
          }
        }}
        placeholder="+ Add required moment (e.g. Ring Exchange, First Dance)"
        className="text-xs px-3 py-1.5 rounded-full bg-black/40 border border-white/10 text-[var(--ivory)] flex-1 placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
      />
      <button
        type="button"
        onClick={() => {
          onAdd(value);
          setValue("");
        }}
        className="btn-secondary px-3 py-1.5 text-xs font-semibold shrink-0"
      >
        Add Moment
      </button>
    </div>
  );
}
