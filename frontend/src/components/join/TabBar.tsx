"use client";

export type JoinTab = "event" | "camera" | "me";

const TABS: Array<{ id: JoinTab; label: string; icon: string }> = [
  { id: "event", label: "Event", icon: "🎉" },
  { id: "camera", label: "Camera", icon: "📷" },
  { id: "me", label: "Me", icon: "🧑" },
];

export function TabBar({
  active,
  onChange,
}: {
  active: JoinTab;
  onChange: (tab: JoinTab) => void;
}) {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 flex items-stretch border-t border-[var(--gold-500)]/30 bg-[var(--bg-1)]/95 backdrop-blur"
      style={{ borderTop: "var(--hairline)" }}
    >
      {TABS.map((tab) => {
        const isActive = tab.id === active;
        const isCamera = tab.id === "camera";
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className="flex-1 flex flex-col items-center gap-1 py-3 min-h-11"
            style={{ color: isActive ? "var(--accent)" : "var(--ink-muted)" }}
          >
            <span
              className={isCamera ? "text-2xl -translate-y-1" : "text-xl"}
              aria-hidden
            >
              {tab.icon}
            </span>
            <span className="text-xs font-medium">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
