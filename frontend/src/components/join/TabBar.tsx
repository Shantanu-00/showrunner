"use client";

import { Sparkles, Camera, User } from "lucide-react";
import type { ElementType } from "react";

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
  return (
    <div className="fixed inset-x-0 bottom-4 z-40 px-4 flex justify-center pointer-events-none">
      <nav
        className="pointer-events-auto flex items-center justify-between gap-1 px-3 py-2 rounded-full glass-pill max-w-md w-full shadow-2xl"
        style={{
          background: "rgba(23, 16, 20, 0.88)",
          borderColor: "rgba(212, 175, 106, 0.3)",
        }}
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
                onClick={() => onChange(tab.id)}
                className="relative -top-3 flex flex-col items-center justify-center p-3 rounded-full btn-primary text-black shadow-lg hover:scale-105 active:scale-95 transition-transform"
                style={{
                  width: "56px",
                  height: "56px",
                  background: "linear-gradient(135deg, var(--accent) 0%, var(--accent-soft) 100%)",
                }}
                aria-label="Open Camera"
              >
                <Icon className="w-6 h-6 stroke-[2.2]" />
              </button>
            );
          }

          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onChange(tab.id)}
              className={`flex-1 flex flex-col items-center justify-center gap-1 py-1.5 px-3 rounded-full transition-all duration-200 ${
                isActive
                  ? "text-[var(--accent)] bg-white/5 font-semibold"
                  : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
              }`}
            >
              <Icon
                className={`w-5 h-5 transition-transform ${
                  isActive ? "scale-110 stroke-[2.2]" : "stroke-[1.8]"
                }`}
              />
              <span className="text-[11px] tracking-wide">{tab.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
