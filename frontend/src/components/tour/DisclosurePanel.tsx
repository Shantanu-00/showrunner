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
            The open event above runs on identical Cloud Run services and identical Firestore
            security rules as any other event. A few <em>values</em> are set differently — and every
            one of them is a setting a real host could also change, never a code path that checks
            whose event this is:
          </p>

          <ul className="space-y-2 pl-4 list-disc text-[var(--ivory-dim)]">
            <li>
              <strong className="text-[var(--ivory)]">Public quality floor:</strong> the ordinary{" "}
              <code>publicFloor</code> field, set to 0.0, so a test photo of a desk still reaches the
              kiosk&rsquo;s just-in strip instead of reading as breakage. Aesthetic score still ranks
              which photograph gets a hero slot.
            </li>
            <li>
              <strong className="text-[var(--ivory)]">Volume caps:</strong> this event has no natural
              end, so a fixed daily and lifetime cap on uploads bounds what it can ever cost, and a
              highlight reel is only re-cut once a set number of new photos and videos have landed
              since the last one — both are ordinary event settings, not a hidden throttle.
            </li>
            <li>
              <strong className="text-[var(--ivory)]">Director cadence:</strong> the Cloud Scheduler
              loop runs at 30 seconds here rather than the production 2 minutes. It is a cron
              expression, and the countdown on this page reads the real one.
            </li>
          </ul>

          <p>
            There is no cast, no script and no pre-loaded photos — every image on the wall arrived
            through the real upload pipeline from someone who actually sent it, from wherever they
            happened to be. The event runs continuously across a multi-week timeline rather than a
            single day, because that is the shape it is actually demonstrating: an agent fleet
            running unattended for as long as content keeps arriving, not a scripted walkthrough.
          </p>

          <p className="text-[11px] text-[var(--ink-faint)] pt-2 border-t border-white/5">
            Face indexing, coverage-gap detection, bounty validation, Lyria soundtracks and safety
            screening all execute against live Google Cloud endpoints. There is no simulated path.
          </p>
        </div>
      )}
    </section>
  );
}
