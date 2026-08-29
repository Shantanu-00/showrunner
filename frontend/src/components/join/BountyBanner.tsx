"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Target, Camera, X, Zap } from "lucide-react";
import type { BountyDoc } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";

const SNOOZE_MS = 10 * 60 * 1000;
const RETREAT_MS = 240;

function useCountdown(endMsIn: number | null | undefined): { fraction: number; expired: boolean } {
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
      className="w-10 h-10 rounded-full shrink-0 flex items-center justify-center p-0.5 shadow-md"
      style={{ background: `conic-gradient(var(--gold-500) ${deg}deg, rgba(212, 175, 106, 0.15) 0deg)` }}
    >
      <div className="w-full h-full rounded-full bg-[var(--bg-1)] flex items-center justify-center text-[var(--accent)]">
        <Target className="w-4 h-4 animate-pulse" />
      </div>
    </div>
  );
}

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
    <div className={`fixed top-4 inset-x-0 z-50 px-4 ${leaving ? "banner-out" : "banner-in"}`}>
      <div
        className="mx-auto max-w-md rounded-3xl p-5 shadow-2xl glass-card border-2"
        style={{
          borderColor: escalated ? "var(--gold-400)" : "rgba(212, 175, 106, 0.4)",
          background: "rgba(23, 16, 20, 0.94)",
          boxShadow: escalated ? "0 0 30px rgba(212, 175, 106, 0.3)" : "var(--shadow-glass)",
        }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 mb-1">
              <Zap className="w-3.5 h-3.5 text-[var(--accent)]" />
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] font-semibold text-[var(--gold-300)]">
                {escalated ? "URGENT STORY DIRECTIVE" : "PHOTO MISSION DETECTED"}
              </span>
            </div>
            <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)] leading-snug">
              {bounty.copy || bounty.title}
            </h3>
          </div>
          <CountdownRing fraction={fraction} />
        </div>

        <div className="flex items-center gap-2.5 mt-4">
          <span className="px-3 py-1.5 rounded-full bg-[var(--gold-500)] text-black font-mono text-xs font-bold tabular-nums shadow-sm flex items-center gap-1">
            <span>+{bounty.points}</span>
            <span className="text-[10px] uppercase font-sans">pts</span>
          </span>
          <button
            type="button"
            onClick={() => {
              onShootNow(bounty.bountyId);
              retreat();
            }}
            className="flex-1 py-2.5 px-4 rounded-full btn-primary text-xs font-semibold flex items-center justify-center gap-1.5"
          >
            <Camera className="w-3.5 h-3.5 stroke-[2.2]" />
            <span>Shoot now</span>
          </button>
          <button
            type="button"
            onClick={retreat}
            aria-label="Dismiss mission"
            className="p-2 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
