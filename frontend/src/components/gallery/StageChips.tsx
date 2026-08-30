"use client";

import { useHaptics } from "@/lib/useHaptics";

export function StageChips({
  stages,
  active,
  onChange,
}: {
  stages: Array<{ stageId: string; label: string }>;
  active: string | null;
  onChange: (stageId: string | null) => void;
}) {
  const { tapHaptic } = useHaptics();

  if (stages.length === 0) return null;

  const handleSelect = (stageId: string | null) => {
    tapHaptic();
    onChange(stageId);
  };

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
