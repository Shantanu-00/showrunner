"use client";

import { dayLabelFromIndex } from "@/lib/eventTime";
import { useHaptics } from "@/lib/useHaptics";

type Stage = { stageId: string; label: string; day?: number | null };

export function StageChips({
  stages,
  active,
  onChange,
}: {
  stages: Stage[];
  active: string | null;
  onChange: (stageId: string | null) => void;
}) {
  const { tapHaptic } = useHaptics();

  if (stages.length === 0) return null;

  const handleSelect = (stageId: string | null) => {
    tapHaptic();
    onChange(stageId);
  };

  const dated = stages.some((s) => typeof s.day === "number" && s.day !== null);

  // Undated events (every pre-spec-13 event) render exactly as before: one row, no grouping.
  if (!dated) {
    return (
      <div className="flex gap-2 overflow-x-auto px-4 pb-2.5 scrollbar-none">
        <Chip
          label="All Phases"
          isActive={active === null}
          onClick={() => handleSelect(null)}
        />
        {stages.map((s) => (
          <Chip
            key={s.stageId}
            label={s.label}
            isActive={active === s.stageId}
            onClick={() => handleSelect(s.stageId)}
          />
        ))}
      </div>
    );
  }

  // Dated (timeline-first) events group chips under a "Day N" header per day; any stage without
  // a day (shouldn't happen once a stage is dated, but stay defensive) rides along "All Phases".
  const byDay = new Map<number, Stage[]>();
  const undated: Stage[] = [];
  for (const s of stages) {
    if (typeof s.day === "number" && s.day !== null) {
      const bucket = byDay.get(s.day) ?? [];
      bucket.push(s);
      byDay.set(s.day, bucket);
    } else {
      undated.push(s);
    }
  }
  const days = Array.from(byDay.keys()).sort((a, b) => a - b);

  return (
    <div className="px-4 pb-2.5">
      <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
        <Chip
          label="All Phases"
          isActive={active === null}
          onClick={() => handleSelect(null)}
        />
        {undated.map((s) => (
          <Chip
            key={s.stageId}
            label={s.label}
            isActive={active === s.stageId}
            onClick={() => handleSelect(s.stageId)}
          />
        ))}
      </div>
      {days.map((day) => (
        <div key={day} className="mb-2">
          <p
            className="text-[11px] uppercase px-1 pb-1"
            style={{ color: "var(--text-secondary)", letterSpacing: "0.08em" }}
          >
            {dayLabelFromIndex(day)}
          </p>
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
            {(byDay.get(day) ?? []).map((s) => (
              <Chip
                key={s.stageId}
                label={s.label}
                isActive={active === s.stageId}
                onClick={() => handleSelect(s.stageId)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Chip({
  label,
  isActive,
  onClick,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`shrink-0 px-4 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all duration-200 cursor-pointer active:scale-95 min-h-[36px] ${
        isActive
          ? "bg-gradient-to-r from-[var(--accent)] to-[var(--accent-soft)] text-slate-950 font-bold shadow-[0_0_16px_-3px_var(--accent-glow)]"
          : "bg-white/5 border border-white/10 text-[var(--text-secondary)] hover:text-white hover:border-white/20"
      }`}
    >
      {label}
    </button>
  );
}
