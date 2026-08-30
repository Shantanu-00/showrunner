"use client";

import { type ReactNode } from "react";

export type StatusVariant = "live" | "syncing" | "idle" | "offline" | "accent";

export interface StatusBadgeProps {
  status?: StatusVariant;
  label?: string;
  count?: number | string | null;
  detail?: string;
  className?: string;
  children?: ReactNode;
}

export function StatusBadge({
  status = "live",
  label = "LIVE",
  count,
  detail,
  className = "",
  children,
}: StatusBadgeProps) {
  const dotColor = {
    live: "bg-[var(--emerald-live)]",
    syncing: "bg-amber-400 animate-pulse",
    idle: "bg-slate-400",
    offline: "bg-red-400",
    accent: "bg-[var(--accent)]",
  }[status];

  return (
    <div
      className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-pill text-xs font-mono tabular-nums tracking-wide text-[var(--text-primary)] shadow-sm backdrop-blur-xl border border-white/10 ${className}`}
    >
      <div className="flex items-center gap-1.5 font-bold uppercase tracking-wider text-[11px]">
        {status === "live" ? (
          <span className="live-dot" />
        ) : (
          <span className={`w-2 h-2 rounded-full ${dotColor}`} />
        )}
        <span className={status === "live" ? "text-[var(--emerald-live)]" : "text-[var(--text-primary)]"}>
          {label}
        </span>
      </div>

      {count !== undefined && count !== null && (
        <>
          <span className="text-white/20" aria-hidden>|</span>
          <span className="text-[var(--text-primary)] font-semibold">{count}</span>
        </>
      )}

      {detail && (
        <>
          <span className="text-white/20" aria-hidden>|</span>
          <span className="text-[var(--text-secondary)] font-normal">{detail}</span>
        </>
      )}

      {children}
    </div>
  );
}
