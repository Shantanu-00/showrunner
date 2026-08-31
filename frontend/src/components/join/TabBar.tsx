"use client";

import { Sparkles, Camera, User } from "lucide-react";
import type { ElementType } from "react";
import { useHaptics } from "@/lib/useHaptics";

export type JoinTab = "event" | "camera" | "me";

const TABS: Array<{ id: JoinTab; label: string; icon: ElementType }> = [
  { id: "event", label: "Gallery", icon: Sparkles },
  { id: "camera", label: "Camera", icon: Camera },
  { id: "me", label: "My Album", icon: User },
];

export function TabBar({
  active,
  onChange,
}: {
  active: JoinTab;
  onChange: (tab: JoinTab) => void;
}) {
  const { tapHaptic } = useHaptics();

  const handleTabChange = (tabId: JoinTab) => {
    tapHaptic();
    onChange(tabId);
  };

  return (
    <div className="fixed inset-x-0 bottom-4 z-40 px-4 flex justify-center pointer-events-none">
      <nav
        role="tablist"
        aria-label="Main navigation"
        className="pointer-events-auto flex items-center justify-between gap-1 p-1.5 rounded-full max-w-sm w-full shadow-[0_16px_40px_rgba(0,0,0,0.85)] backdrop-blur-2xl bg-slate-950/85 border border-white/10"
      >
        {TABS.map((tab) => {
          const isActive = tab.id === active;
          const isCamera = tab.id === "camera";
          const Icon = tab.icon;

          if (isCamera) {
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => handleTabChange(tab.id)}
                className="relative -top-3.5 flex flex-col items-center justify-center rounded-full text-slate-950 shadow-[0_0_25px_-2px_var(--accent-glow)] hover:shadow-[0_0_35px_2px_var(--accent-glow)] hover:scale-105 active:scale-95 transition-all duration-300 cursor-pointer border border-white/20"
                style={{
                  width: "54px",
                  height: "54px",
                  background: "linear-gradient(135deg, var(--accent) 0%, var(--accent-soft) 100%)",
                }}
                aria-label="Open Camera and Capture Photo"
              >
                <Icon className="w-6 h-6 stroke-[2.4]" />
              </button>
            );
          }

          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => handleTabChange(tab.id)}
              className={`relative flex-1 flex flex-col items-center justify-center gap-0.5 py-1.5 px-3 rounded-full transition-all duration-200 min-h-[42px] active:scale-[0.96] cursor-pointer ${
                isActive
                  ? "text-white font-semibold"
                  : "text-[var(--text-secondary)] hover:text-white"
              }`}
            >
              {isActive && (
                <span
                  className="absolute inset-0 rounded-full bg-white/10 border border-white/15 shadow-sm transition-all duration-300"
                  aria-hidden
                />
              )}
              <Icon
                className={`relative z-10 w-4.5 h-4.5 transition-transform duration-200 ${
                  isActive ? "scale-110 text-[var(--accent)] stroke-[2.2]" : "stroke-[1.8]"
                }`}
              />
              <span className={`relative z-10 text-[10px] tracking-wide font-medium ${isActive ? "text-[var(--accent)] font-semibold" : ""}`}>
                {tab.label}
              </span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
