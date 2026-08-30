"use client";

import { useEffect, useMemo, useState } from "react";
import { Plus, Calendar, AlertTriangle, Check } from "lucide-react";
import { parseItinerary, saveStages } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type {
  EventStageDoc,
  ExpectedSetting,
  HostEventDoc,
  RequiredMoment,
  StageTheme,
} from "@/lib/hostTypes";
import { dateForDayIndex, dayIndexFromLocalDate, formatLocalDate, slugify } from "@/lib/hostTypes";
import { ItineraryInputTabs, type ItineraryParsePayload } from "./ItineraryInputTabs";
import { StageEditorCard } from "./StageEditorCard";

interface DraftStage {
  key: string;
  stageId: string;
  label: string;
  timeHint?: string;
  startsAt: string;
  endsAt: string;
  requiredMoments: RequiredMoment[];
  expectedSetting: ExpectedSetting | "";
  theme: StageTheme | "";
}

function fromEventStage(s: EventStageDoc, i: number): DraftStage {
  return {
    key: `${s.stageId}-${i}`,
    stageId: s.stageId,
    label: s.label,
    // Naive truncation of the stored UTC ISO instant — kept exactly as this panel always did it,
    // not corrected to a real timezone conversion here (see `hostTypes.ts`'s day-grouping note).
    startsAt: s.startsAt ? s.startsAt.slice(0, 16) : "",
    endsAt: s.endsAt ? s.endsAt.slice(0, 16) : "",
    requiredMoments: s.requiredMoments ?? [],
    expectedSetting: s.expectedSetting ?? "",
    theme: (s.theme as StageTheme) ?? "",
  };
}

export function ItineraryPanel({ event, eventId }: { event: HostEventDoc; eventId: string }) {
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

  async function handleParse(payload: ItineraryParsePayload) {
    setParsing(true);
    setError(null);
    try {
      const out = await parseItinerary(eventId, payload);
      setStages(
        out.stages.map((s, i) => ({
          key: `${s.stageId}-${i}-${Date.now()}`,
          stageId: s.stageId,
          label: s.label,
          timeHint: s.timeHint,
          startsAt: s.proposedStartLocal || "",
          endsAt: s.proposedEndLocal || "",
          requiredMoments: s.requiredMoments,
          expectedSetting: s.expectedSetting ?? "",
          theme: "",
        }))
      );
      setWarnings(out.warnings);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `Couldn't parse that (${err.status}): ${err.message} — you can still add stages manually below.`
          : "Something went wrong."
      );
    } finally {
      setParsing(false);
    }
  }

  function addStage() {
    setStages((prev) => [
      ...prev,
      {
        key: `new-${Date.now()}`,
        stageId: "",
        label: "New Stage",
        startsAt: "",
        endsAt: "",
        requiredMoments: [],
        expectedSetting: "",
        theme: "",
      },
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
        theme: s.theme || null,
        // "" means the host declared nothing. Sent as null, not omitted: the backend enum is
        // `SceneSetting | None`, and an empty string is not a member of it.
        expectedSetting: s.expectedSetting || null,
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

  // Day-grouped only when the event actually declared a calendar span (spec 13) — an undated event
  // (every event created before it, or a host who skipped the field) keeps the flat list it always had.
  const groups = useMemo(() => {
    if (!event.startsOn) return null;
    const byDay = new Map<number | null, DraftStage[]>();
    for (const s of stages) {
      const idx = dayIndexFromLocalDate(event.startsOn, s.startsAt);
      const arr = byDay.get(idx) ?? [];
      arr.push(s);
      byDay.set(idx, arr);
    }
    const keys = Array.from(byDay.keys()).sort((a, b) => {
      if (a === null) return 1;
      if (b === null) return -1;
      return a - b;
    });
    return keys.map((dayIndex) => ({ dayIndex, stages: byDay.get(dayIndex) as DraftStage[] }));
  }, [stages, event.startsOn]);

  const positionOf = new Map(stages.map((s, i) => [s.key, i]));

  function stageCard(stage: DraftStage) {
    const idx = positionOf.get(stage.key) ?? 0;
    return (
      <StageEditorCard
        key={stage.key}
        position={idx + 1}
        label={stage.label}
        timeHint={stage.timeHint}
        startsAt={stage.startsAt}
        endsAt={stage.endsAt}
        requiredMoments={stage.requiredMoments}
        expectedSetting={stage.expectedSetting}
        theme={stage.theme}
        canEdit={canEdit}
        onLabelChange={(v) => updateStage(stage.key, { label: v })}
        onStartsAtChange={(v) => updateStage(stage.key, { startsAt: v })}
        onEndsAtChange={(v) => updateStage(stage.key, { endsAt: v })}
        onExpectedSettingChange={(v) => updateStage(stage.key, { expectedSetting: v })}
        onThemeChange={(v) => updateStage(stage.key, { theme: v })}
        onAddMoment={(label) => addMoment(stage.key, label)}
        onRemoveMoment={(momentId) => removeMoment(stage.key, momentId)}
        onRemove={() => removeStage(stage.key)}
      />
    );
  }

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
          <ItineraryInputTabs onParse={(p) => void handleParse(p)} busy={parsing} />
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

      <div className="space-y-6 mb-6">
        {groups ? (
          groups.map((group) => (
            <div key={String(group.dayIndex)} className="space-y-4">
              <div className="flex items-center gap-2 pt-1">
                <Calendar className="w-3.5 h-3.5 text-[var(--gold-300)]" />
                <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--gold-300)]">
                  {group.dayIndex !== null
                    ? `Day ${group.dayIndex} — ${formatLocalDate(dateForDayIndex(event.startsOn as string, group.dayIndex))}`
                    : "Unscheduled"}
                </h4>
              </div>
              <div className="space-y-4">{group.stages.map((s) => stageCard(s))}</div>
            </div>
          ))
        ) : (
          <div className="space-y-4">{stages.map((s) => stageCard(s))}</div>
        )}
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
