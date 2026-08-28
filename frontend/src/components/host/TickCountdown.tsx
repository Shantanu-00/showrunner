"use client";

import { useEffect, useState } from "react";
import { listenDirectorState } from "@/lib/hostFirestore";
import type { DirectorTickState, HostEventDoc } from "@/lib/hostTypes";

/** Spec 09 §2's two Cloud Scheduler cadences, mirrored here the same way `VIP_WEIGHT` mirrors
 * a backend enum in `lib/types.ts` — not values this client invents. Production `director-tick`
 * runs on a 2-minute cron; `class=='protected_demo'` events are additionally ticked every minute
 * with a +30s interleave (`director-tick-demo`, `shared/settings.py::DEMO_INTERLEAVE_SECONDS`),
 * so their effective cadence is the shorter of the two. */
const DEMO_CADENCE_SEC = 30;
const PRODUCTION_CADENCE_SEC = 120;

/** EXECUTION-PLAN §7e row 11: a live next-tick countdown replaces the "Run director now" button
 * on the demo's happy path — the mechanism (this component) is what S14's `/judge` page reuses.
 * Every number here traces to a real Firestore write from a real Scheduler-triggered tick
 * (`ledger/directorState.lastTickAt`, `backend/directors/story/session.py`); nothing is invented. */
export function TickCountdown({ eventId, eventClass }: { eventId: string; eventClass: HostEventDoc["class"] }) {
  const [state, setState] = useState<DirectorTickState | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => listenDirectorState(eventId, setState, () => {}), [eventId]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const cadenceSec = eventClass === "protected_demo" ? DEMO_CADENCE_SEC : PRODUCTION_CADENCE_SEC;

  let value = "—";
  let caption = "no autonomous tick yet";
  if (state?.lastTickAtMs) {
    const secondsRemaining = Math.max(0, Math.round((state.lastTickAtMs + cadenceSec * 1000 - nowMs) / 1000));
    value = secondsRemaining > 0 ? `${secondsRemaining}s` : "due any second";
    caption = `${state.tickCount} tick${state.tickCount === 1 ? "" : "s"} so far, nobody pressing anything`;
  }

  return (
    <div className="rounded-[var(--radius-card)] p-3" style={{ background: "var(--bg-1)", border: "var(--hairline)" }}>
      <p className="text-xs mb-1" style={{ color: "var(--ink-muted)" }}>
        Next director tick
      </p>
      <p className="font-mono tabular-nums text-xl" style={{ color: "var(--ivory)" }}>
        {value}
      </p>
      <p className="text-[10px] mt-1" style={{ color: "var(--ink-muted)" }}>
        {caption}
      </p>
    </div>
  );
}
