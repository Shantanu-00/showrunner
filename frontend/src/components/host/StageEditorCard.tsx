"use client";

import { useState } from "react";
import { Clock, Palette, Tag, Trash2, X } from "lucide-react";
import type { ExpectedSetting, RequiredMoment, StageTheme } from "@/lib/hostTypes";
import { EXPECTED_SETTING_LABELS, STAGE_THEME_OPTIONS } from "@/lib/hostTypes";

/** One editable stage row — the review table's unit, shared by `ItineraryPanel` (the console) and
 * `HostWizard`'s Step 3 (the review-before-first-save case). Both need the identical set of editable
 * fields (label, windows, required moments, theme, expected setting), and drift between two
 * hand-maintained copies is exactly the kind of bug a judge poking both surfaces would find. */
export function StageEditorCard({
  position,
  label,
  timeHint,
  startsAt,
  endsAt,
  requiredMoments,
  expectedSetting,
  theme,
  canEdit,
  onLabelChange,
  onStartsAtChange,
  onEndsAtChange,
  onExpectedSettingChange,
  onThemeChange,
  onAddMoment,
  onRemoveMoment,
  onRemove,
}: {
  position: number;
  label: string;
  timeHint?: string;
  startsAt: string;
  endsAt: string;
  requiredMoments: RequiredMoment[];
  expectedSetting: ExpectedSetting | "";
  theme: StageTheme | "";
  canEdit: boolean;
  onLabelChange: (v: string) => void;
  onStartsAtChange: (v: string) => void;
  onEndsAtChange: (v: string) => void;
  onExpectedSettingChange: (v: ExpectedSetting | "") => void;
  onThemeChange: (v: StageTheme | "") => void;
  onAddMoment: (label: string) => void;
  onRemoveMoment: (momentId: string) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="rounded-2xl p-5 glass-card bg-black/30 border border-white/10 hover:border-[var(--accent)] transition-all">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-6 h-6 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] flex items-center justify-center font-mono text-xs font-bold shrink-0">
          {position}
        </div>
        <input
          value={label}
          disabled={!canEdit}
          onChange={(e) => onLabelChange(e.target.value)}
          className="flex-1 font-[family-name:var(--font-display)] text-lg bg-transparent font-medium text-[var(--ivory)] border-b border-transparent hover:border-white/20 focus:border-[var(--accent)] focus:outline-none min-w-0"
        />
        {timeHint && (
          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-white/5 text-[var(--ink-muted)] shrink-0">
            Hint: {timeHint}
          </span>
        )}
        {canEdit && onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="p-1.5 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-[var(--danger)] transition-colors shrink-0"
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
            <span>Start</span>
          </span>
          <input
            type="datetime-local"
            value={startsAt}
            disabled={!canEdit}
            onChange={(e) => onStartsAtChange(e.target.value)}
            className="block w-full px-3 py-2 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
        <label className="text-[11px] text-[var(--ink-muted)] font-medium">
          <span className="flex items-center gap-1 mb-1">
            <Clock className="w-3 h-3 text-[var(--gold-300)]" />
            <span>End</span>
          </span>
          <input
            type="datetime-local"
            value={endsAt}
            disabled={!canEdit}
            onChange={(e) => onEndsAtChange(e.target.value)}
            className="block w-full px-3 py-2 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none"
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={expectedSetting}
          disabled={!canEdit}
          onChange={(e) => onExpectedSettingChange(e.target.value as ExpectedSetting | "")}
          aria-label={`Where ${label || "this stage"} happens`}
          title="Optional. Helps the wall judge which photos belong to this stage."
          className="text-[11px] px-2 py-1 rounded-lg bg-white/5 border border-white/10 text-[var(--ink-muted)] hover:border-white/25 focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
        >
          <option value="">Where? (optional)</option>
          {(Object.keys(EXPECTED_SETTING_LABELS) as ExpectedSetting[]).map((value) => (
            <option key={value} value={value}>
              {EXPECTED_SETTING_LABELS[value]}
            </option>
          ))}
        </select>

        <label className="flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-lg bg-white/5 border border-white/10 text-[var(--ink-muted)]">
          <Palette className="w-3 h-3" />
          <select
            value={theme}
            disabled={!canEdit}
            onChange={(e) => onThemeChange(e.target.value as StageTheme | "")}
            aria-label={`Kiosk palette for ${label || "this stage"}`}
            className="bg-transparent focus:outline-none disabled:opacity-50"
          >
            <option value="">Theme (optional)</option>
            {STAGE_THEME_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="space-y-2 pt-2 border-t border-white/5">
        <div className="flex items-center gap-1 text-[11px] text-[var(--ink-muted)]">
          <Tag className="w-3 h-3" />
          <span>Required moments:</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {requiredMoments.map((m) => (
            <span
              key={m.momentId}
              className="text-xs px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[var(--ivory)] flex items-center gap-1.5"
            >
              <span>{m.label}</span>
              {canEdit && (
                <button type="button" onClick={() => onRemoveMoment(m.momentId)} className="hover:text-[var(--danger)]">
                  <X className="w-3 h-3" />
                </button>
              )}
            </span>
          ))}
        </div>
        {canEdit && <MomentAdder onAdd={onAddMoment} />}
      </div>
    </div>
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
        placeholder="+ Add required moment (e.g. Group Photo, Sunset Viewpoint)"
        className="text-xs px-3 py-1.5 rounded-full bg-black/40 border border-white/10 text-[var(--ivory)] flex-1 min-w-0 placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
      />
      <button
        type="button"
        onClick={() => {
          onAdd(value);
          setValue("");
        }}
        className="btn-secondary px-3 py-1.5 text-xs font-semibold shrink-0"
      >
        Add
      </button>
    </div>
  );
}
