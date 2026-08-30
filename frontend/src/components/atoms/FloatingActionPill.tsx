"use client";

import React, { type ReactNode } from "react";
import { useHaptics } from "@/lib/useHaptics";

export interface PillTab<T extends string = string> {
  id: T;
  label: string;
  icon?: React.ElementType;
  badge?: string | number;
}

export function FloatingActionPill<T extends string>({
  tabs,
  active,
  onChange,
  className = "",
  centerAction,
}: {
  tabs: PillTab<T>[];
  active: T;
  onChange: (id: T) => void;
  className?: string;
  centerAction?: ReactNode;
}) {
  const { tapHaptic } = useHaptics();

  const handleSelect = (id: T) => {
    tapHaptic();
    onChange(id);
  };

  return (
    <div className={`fixed inset-x-0 bottom-4 z-40 px-4 flex justify-center pointer-events-none ${className}`}>
      <nav
        role="tablist"
        aria-label="Navigation"
        className="pointer-events-auto flex items-center justify-between gap-1.5 p-1.5 rounded-full glass-pill max-w-md w-full shadow-2xl backdrop-blur-2xl bg-slate-950/80 border border-white/10"
      >
        {tabs.map((tab, idx) => {
          const isActive = tab.id === active;
          const Icon = tab.icon;

          // If center action slot is provided and we are at the middle
          const isMiddle = tabs.length % 2 === 0 && idx === tabs.length / 2;

          return (
            <React.Fragment key={tab.id}>
              {isMiddle && centerAction && <div className="shrink-0">{centerAction}</div>}
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => handleSelect(tab.id)}
                className={`relative flex-1 flex flex-col items-center justify-center gap-1 py-2 px-3 rounded-full transition-all duration-250 active:scale-[0.95] min-h-[44px] ${
                  isActive
                    ? "text-white font-semibold shadow-inner"
                    : "text-[var(--text-secondary)] hover:text-white"
                }`}
              >
                {/* Active Spring Indicator Surface */}
                {isActive && (
                  <span
                    className="absolute inset-0 rounded-full bg-white/10 border border-white/15 shadow-md transition-all duration-300"
                    aria-hidden
                  />
                )}

                {Icon && (
                  <Icon
                    className={`relative z-10 w-5 h-5 transition-transform duration-200 ${
                      isActive ? "scale-110 text-[var(--accent)] stroke-[2.2]" : "stroke-[1.8]"
                    }`}
                  />
                )}
                <span className="relative z-10 text-[11px] tracking-wide font-medium">
                  {tab.label}
                </span>

                {tab.badge !== undefined && (
                  <span className="relative z-10 ml-1 px-1.5 py-0.2 rounded-full text-[10px] font-mono tabular-nums bg-[var(--accent)] text-black font-bold">
                    {tab.badge}
                  </span>
                )}
              </button>
            </React.Fragment>
          );
        })}
      </nav>
    </div>
  );
}
