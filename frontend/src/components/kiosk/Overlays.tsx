"use client";

import { useEffect, useState } from "react";
import { Sparkles, Radio } from "lucide-react";
import { JoinQr } from "./JoinQr";

export function MonogramAndQr({
  eventName,
  stageLabel,
  joinUrl,
  qrSizePx = 140,
}: {
  eventName: string;
  stageLabel?: string | null;
  joinUrl: string;
  qrSizePx?: number;
}) {
  return (
    <div className="absolute bottom-[3%] left-[3%] z-30 flex items-end gap-4 p-3 rounded-2xl glass-card backdrop-blur-xl border border-white/10 shadow-2xl">
      <JoinQr url={joinUrl} sizePx={qrSizePx} />
      <div className="pr-2">
        <h3 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-gold-gradient">
          {eventName}
        </h3>
        {stageLabel && (
          <p className="text-xs font-mono font-medium text-[var(--accent)] mt-0.5 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)]" />
            <span>{stageLabel}</span>
          </p>
        )}
      </div>
    </div>
  );
}

export function LiveStatusGlyph({
  publicCount,
  updatedAt,
  leaseHeld,
}: {
  publicCount: number | null;
  updatedAt: number | null;
  leaseHeld: boolean;
}) {
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const agoSec = updatedAt ? Math.max(0, Math.round((nowTick - updatedAt) / 1000)) : null;

  return (
    <div
      className="absolute bottom-[3%] right-[3%] z-30 font-mono tabular-nums text-xs px-4 py-2 rounded-full glass-card backdrop-blur-xl border border-white/10 shadow-2xl flex items-center gap-2.5 text-[var(--ivory)]"
    >
      <div className="flex items-center gap-1.5 text-[var(--ok)] font-bold">
        <span className="live-dot" />
        <span>LIVE</span>
      </div>
      <span className="text-white/20">|</span>
      <span className="text-[var(--gold-300)] font-semibold">
        {publicCount === null ? "—" : publicCount.toLocaleString()} photos
      </span>
      <span className="text-white/20">|</span>
      <span className="text-[var(--ink-muted)]">agent directed</span>
      <span className="text-white/20">|</span>
      <span className="text-[var(--ink-muted)]">
        {agoSec === null ? "—" : `sync ${agoSec}s ago`}
      </span>
    </div>
  );
}
