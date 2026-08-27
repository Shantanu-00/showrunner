"use client";

export function StageChips({
  stages,
  active,
  onChange,
}: {
  stages: Array<{ stageId: string; label: string }>;
  active: string | null;
  onChange: (stageId: string | null) => void;
}) {
  if (stages.length === 0) return null;
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
