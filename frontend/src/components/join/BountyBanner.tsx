"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { BountyDoc } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";

/** Spec 05 §4's client-side anti-spam gate: at most one banner per guest per 10 minutes,
 * regardless of how many bounties are active. Escalation does not bypass it — the escalation
 * itself is a kiosk-side signal (spec 04 §4's takeover slot), not a second interruption pass at
 * a guest who has already been asked once. */
const SNOOZE_MS = 10 * 60 * 1000;
/** Spec 12 §7: "quiet 240ms retreat" on expiry/dismiss — matches `--dur-standard` in tokens.css. */
const RETREAT_MS = 240;

function useCountdown(endMsIn: number | null | undefined): { fraction: number; expired: boolean } {
  // Epoch millis, normalised by `listenActiveBounties`. This used to take the raw `expiresAt` and
  // call `new Date(...)` on it, which for a Firestore `Timestamp` yields NaN -- and NaN is falsy, so
  // the guard below silently returned a permanently-full ring instead of counting down.
  const endMs = useMemo(() => (typeof endMsIn === "number" && Number.isFinite(endMsIn) ? endMsIn : null), [endMsIn]);
  const startRef = useRef<number>(Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    startRef.current = Date.now();
    if (!endMs) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [endMs]);

  if (!endMs) return { fraction: 1, expired: false };
  const total = Math.max(1, endMs - startRef.current);
  const fraction = Math.max(0, Math.min(1, (endMs - now) / total));
  return { fraction, expired: now >= endMs };
}

function CountdownRing({ fraction }: { fraction: number }) {
  const deg = Math.max(0, Math.min(1, fraction)) * 360;
  return (
    <div
      aria-hidden
      className="w-8 h-8 rounded-full shrink-0"
      style={{ background: `conic-gradient(var(--gold-500) ${deg}deg, rgb(212 175 106 / 0.15) 0deg)` }}
    />
  );
}

/** The mission-briefing banner (spec 12 §7) — slides down over any tab, one bounty at a time.
 * `onShootNow` hands the tap back to `JoinShell`, which is what actually opens the camera and
 * stamps `bountyId` onto the resulting upload batch (spec 01 §3, spec 05 §3). */
export function BountyBanner({
  eventId,
  onShootNow,
}: {
  eventId: string;
  onShootNow: (bountyId: string) => void;
}) {
  const [bounties, setBounties] = useState<BountyDoc[]>([]);
  const [shownId, setShownId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const lastShownAtRef = useRef<number>(0);
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(
    () => listenActiveBounties(eventId, setBounties, () => setBounties([])),
    [eventId]
  );

  useEffect(() => {
    if (shownId || leaving) return;
    if (Date.now() - lastShownAtRef.current < SNOOZE_MS) return;
    const candidate = [...bounties]
      .filter((b) => !seenRef.current.has(b.bountyId))
      .sort((a, b) => (b.createdAtMs ?? 0) - (a.createdAtMs ?? 0))[0];
    if (!candidate) return;
    setShownId(candidate.bountyId);
    lastShownAtRef.current = Date.now();
    seenRef.current.add(candidate.bountyId);
    if (typeof navigator !== "undefined" && navigator.vibrate) navigator.vibrate([30, 40, 30]);
  }, [bounties, shownId, leaving]);

  const bounty = bounties.find((b) => b.bountyId === shownId) ?? null;
  const { fraction, expired } = useCountdown(bounty?.expiresAtMs);

  function retreat() {
    setLeaving(true);
    window.setTimeout(() => {
      setShownId(null);
      setLeaving(false);
    }, RETREAT_MS);
  }

  useEffect(() => {
    if (bounty && expired && !leaving) retreat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expired]);

  if (!bounty) return null;

  const escalated = bounty.status === "escalated";

  return (
    <div className={`fixed top-0 inset-x-0 z-50 px-3 pt-3 ${leaving ? "banner-out" : "banner-in"}`}>
      <div
        className="mx-auto max-w-md rounded-[var(--radius-banner)] p-4"
        style={{
          background: "var(--bg-glass)",
          backdropFilter: "blur(16px)",
          border: escalated ? "1px solid var(--gold-500)" : "var(--hairline)",
        }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="font-mono text-xs tracking-[0.2em]" style={{ color: "var(--gold-300)" }}>
              {escalated ? "THE DIRECTOR NEEDS THIS" : "THE DIRECTOR ASKS"}
            </p>
            <p
              className="font-[family-name:var(--font-display)] text-xl mt-1"
              style={{ color: "var(--ivory)" }}
            >
              {bounty.copy || bounty.title}
            </p>
          </div>
          <CountdownRing fraction={fraction} />
        </div>

        <div className="flex items-center gap-3 mt-4">
          <span
            className="px-3 py-1.5 rounded-[var(--radius-pill)] font-mono text-sm tabular-nums"
            style={{ background: "var(--gold-500)", color: "var(--bg-0)" }}
          >
            +{bounty.points}
          </span>
          <button
            type="button"
            onClick={() => {
              onShootNow(bounty.bountyId);
              retreat();
            }}
            className="flex-1 py-2.5 rounded-[var(--radius-pill)] font-medium"
            style={{ background: "var(--accent)", color: "var(--bg-0)" }}
          >
            Shoot now
          </button>
          <button
            type="button"
            onClick={retreat}
            aria-label="Dismiss"
            className="text-lg px-1"
            style={{ color: "var(--ink-muted)" }}
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}
