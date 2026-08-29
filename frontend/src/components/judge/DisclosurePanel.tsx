"use client";

import { useState } from "react";
import { Info, ChevronDown, ChevronUp } from "lucide-react";

export function DisclosurePanel() {
  const [open, setOpen] = useState(false);

  return (
    <section className="rounded-2xl glass-card overflow-hidden border border-white/10 shadow-lg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full text-left px-5 py-3.5 flex items-center justify-between gap-3 hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Info className="w-4 h-4 text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--ivory)]">
            About this demo environment
          </span>
        </div>
        <div className="flex items-center gap-1 text-xs font-mono text-[var(--accent)]">
          <span>{open ? "Collapse" : "Expand"}</span>
          {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {open && (
        <div className="px-5 pb-5 pt-2 space-y-3 text-xs text-[var(--ink-muted)] leading-relaxed border-t border-white/5">
          <p>
            This demo runs on identical Cloud Run microservices and Firestore security rules as production events. Three parameters are configured for hackathon demonstration:
          </p>

          <ul className="space-y-2 pl-4 list-disc text-[var(--ivory-dim)]">
            <li>
              <strong className="text-[var(--ivory)]">Public Quality Floor:</strong> Set to 0.0 for this demo event so test uploads of desks/badges appear immediately in the kiosk strip. Aesthetic rank continues to control hero presentation slots.
            </li>
            <li>
              <strong className="text-[var(--ivory)]">Compressed Timeline Windows:</strong> Stage windows are configured in minutes rather than hours to demonstrate automatic stage transitions in a short judging session.
            </li>
            <li>
              <strong className="text-[var(--ivory)]">Accelerated Director Cadence:</strong> Cloud Scheduler runs at an accelerated 30-second loop to provide fast feedback during demonstration.
            </li>
          </ul>

          <p className="text-[11px] text-[var(--ink-faint)] pt-2 border-t border-white/5">
            All facial recognition, story gap detection, Lyria reel commissions, and safety screenings execute against genuine live GCP endpoints.
          </p>
        </div>
      )}
    </section>
  );
}
