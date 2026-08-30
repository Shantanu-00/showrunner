"use client";

import { dayLabelFromIndex } from "@/lib/eventTime";

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
  if (stages.length === 0) return null;

  const dated = stages.some((s) => typeof s.day === "number" && s.day !== null);

  // Undated events (the common case today) render exactly as before: one row, no grouping.
  if (!dated) {
    return (
      <div className="flex gap-2 overflow-x-auto px-4 pb-1 -mx-1">
        <Chip label="All" isActive={active === null} onClick={() => onChange(null)} />
        {stages.map((s) => (
          <Chip
            key={s.stageId}
            label={s.label}
            isActive={active === s.stageId}
            onClick={() => onChange(s.stageId)}
          />
        ))}
      </div>
    );
  }

  // Dated (timeline-first) events group chips under a "Day N" header per day; any stage without
  // a day (shouldn't happen once a stage is dated, but stay defensive) rides along "All".
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
    <div className="px-4 pb-1">
      <div className="flex gap-2 overflow-x-auto -mx-1 pb-2">
        <Chip label="All" isActive={active === null} onClick={() => onChange(null)} />
        {undated.map((s) => (
          <Chip
            key={s.stageId}
            label={s.label}
            isActive={active === s.stageId}
            onClick={() => onChange(s.stageId)}
          />
        ))}
      </div>
      {days.map((day) => (
        <div key={day} className="mb-2">
          <p
            className="text-[11px] uppercase px-1 pb-1"
            style={{ color: "var(--ink-muted)", letterSpacing: "0.08em" }}
          >
            {dayLabelFromIndex(day)}
          </p>
          <div className="flex gap-2 overflow-x-auto -mx-1 pb-1">
            {(byDay.get(day) ?? []).map((s) => (
              <Chip
                key={s.stageId}
                label={s.label}
                isActive={active === s.stageId}
                onClick={() => onChange(s.stageId)}
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
      className="shrink-0 px-4 py-2 rounded-[var(--radius-pill)] text-sm whitespace-nowrap min-h-11"
      style={{
        border: isActive ? "2px solid var(--accent)" : "var(--hairline)",
        color: isActive ? "var(--accent)" : "var(--ink-muted)",
        background: "var(--bg-1)",
      }}
    >
      {label}
    </button>
  );
}
