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
    <div className="flex gap-2 overflow-x-auto px-4 pb-2 scrollbar-none">
      <Chip label="All Phases" isActive={active === null} onClick={() => onChange(null)} />
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
      className={`shrink-0 px-4 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all duration-200 ${
        isActive
          ? "bg-[var(--accent)] text-black font-semibold shadow-md"
          : "bg-white/5 border border-white/10 text-[var(--ink-muted)] hover:text-[var(--ivory)] hover:border-white/20"
      }`}
    >
      {label}
    </button>
  );
}
