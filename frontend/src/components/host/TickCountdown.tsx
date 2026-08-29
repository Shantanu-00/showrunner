"use client";

import { useEffect, useRef, useState } from "react";
import { listenDirectorState } from "@/lib/hostFirestore";
import type { DirectorTickState, HostEventDoc } from "@/lib/hostTypes";

/** Spec 09 §2's two Cloud Scheduler cadences. The **host** console derives these client-side from
 * `event.class`, which it is allowed to read; the `/judge` page cannot (it is anonymous, and
 * `ledger/` is host-only in `firestore.rules`), so it gets `cadenceSec` from the server instead —
 * `GET /v1/events/{id}/public`'s `director` block, which is HANDOFF §4.22's prescribed read path.
 * Production `director-tick` runs on a 2-minute cron; `class=='protected_demo'` events are
 * additionally ticked every minute with a +30s interleave (`director-tick-demo`,
 * `shared/settings.py::DEMO_INTERLEAVE_SECONDS`), so their effective cadence is the shorter. */
const DEMO_CADENCE_SEC = 30;
const PRODUCTION_CADENCE_SEC = 120;

export function cadenceForClass(eventClass: HostEventDoc["class"]): number {
  return eventClass === "protected_demo" ? DEMO_CADENCE_SEC : PRODUCTION_CADENCE_SEC;
}

/** The presentational half, shared by the host console (Firestore listener) and `/judge` (one REST
 * read). Kept separate because the two surfaces have different *read paths* and identical maths —
 * duplicating the arithmetic is how the two would eventually disagree about what a tick means. */
export function TickCountdownView({
  lastTickAtMs,
  tickCount,
  cadenceSec,
  onDue,
}: {
  lastTickAtMs: number | null;
  tickCount: number;
  cadenceSec: number;
  /** Fired once each time the countdown crosses zero — `/judge` uses it to re-read the tick
   * numbers. Not a poll: nothing is requested while the countdown is still running. */
  onDue?: () => void;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const firedFor = useRef<number | null>(null);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const dueAt = lastTickAtMs ? lastTickAtMs + cadenceSec * 1000 : null;
  const secondsRemaining = dueAt ? Math.max(0, Math.round((dueAt - nowMs) / 1000)) : null;

  useEffect(() => {
    if (!onDue || !dueAt || secondsRemaining !== 0) return;
    if (firedFor.current === dueAt) return; // once per due instant, not once per second past it
    firedFor.current = dueAt;
    onDue();
  }, [onDue, dueAt, secondsRemaining]);

  let value = "—";
  let caption = "no autonomous tick yet";
  if (secondsRemaining !== null) {
    value = secondsRemaining > 0 ? `${secondsRemaining}s` : "due any second";
    caption = `${tickCount} tick${tickCount === 1 ? "" : "s"} so far, nobody pressing anything`;
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

/** EXECUTION-PLAN §7e row 11: a live next-tick countdown replaces the "Run director now" button
 * on the demo's happy path. Every number traces to a real Firestore write from a real
 * Scheduler-triggered tick (`ledger/directorState.lastTickAt`,
 * `backend/directors/story/session.py`); nothing is invented. */
export function TickCountdown({ eventId, eventClass }: { eventId: string; eventClass: HostEventDoc["class"] }) {
  const [state, setState] = useState<DirectorTickState | null>(null);

  useEffect(() => listenDirectorState(eventId, setState, () => {}), [eventId]);

  return (
    <TickCountdownView
      lastTickAtMs={state?.lastTickAtMs ?? null}
      tickCount={state?.tickCount ?? 0}
      cadenceSec={cadenceForClass(eventClass)}
    />
  );
}
