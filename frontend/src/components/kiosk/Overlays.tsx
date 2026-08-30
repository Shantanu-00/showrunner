"use client";

import { useEffect, useState } from "react";
import { Sparkles, QrCode, ArrowUpRight } from "lucide-react";
import { JoinQr } from "./JoinQr";
import { StatusBadge } from "@/components/atoms/StatusBadge";

export function LiveBroadcastHeader({
  eventName,
  stageLabel,
  className = "",
}: {
  eventName: string;
  stageLabel?: string | null;
  className?: string;
}) {
  return (
    <header
      className={`absolute top-[3%] inset-x-[3%] z-30 flex items-center justify-between pointer-events-none transition-opacity duration-500 ${className}`}
    >
      {/* Animated LIVE SHOWCASE Emerald Badge */}
      <div className="pointer-events-auto flex items-center gap-3">
        <StatusBadge status="live" label="LIVE SHOWCASE" className="border-emerald-500/30 shadow-lg" />
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full glass-card border border-white/10 text-xs font-mono text-[var(--text-secondary)]">
          <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span>AI Director Stream</span>
        </div>
      </div>

      {/* Event Title & Active Stage Pill */}
      <div className="pointer-events-auto flex items-center gap-2.5 px-4 py-2 rounded-full glass-card backdrop-blur-2xl border border-white/10 shadow-2xl">
        <h2 className="font-[family-name:var(--font-display)] text-sm sm:text-base font-semibold text-gold-gradient tracking-tight">
          {eventName}
        </h2>
        {stageLabel && (
          <>
            <span className="text-white/20" aria-hidden>|</span>
            <span className="text-[11px] font-mono font-medium text-[var(--accent)] flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-pulse" />
              <span>{stageLabel}</span>
            </span>
          </>
        )}
      </div>
    </header>
  );
}

/** Demo QR Badge in bottom corner: "Scan to find your photos" */
export function DemoQrBadge({
  joinUrl,
  qrSizePx = 110,
  className = "",
}: {
  joinUrl: string;
  qrSizePx?: number;
  className?: string;
}) {
  return (
    <aside
      aria-label="Scan to find your photos"
      className={`absolute bottom-[3%] left-[3%] z-30 flex items-center gap-3.5 p-3 rounded-3xl glass-card backdrop-blur-2xl bg-slate-950/85 border border-white/15 shadow-2xl transition-opacity duration-500 max-w-xs ${className}`}
    >
      <div className="shrink-0 p-1 rounded-2xl bg-white shadow-inner">
        <JoinQr url={joinUrl} sizePx={qrSizePx} />
      </div>
      <div className="pr-1.5 flex flex-col justify-center">
        <div className="flex items-center gap-1 text-[10px] font-mono uppercase tracking-[0.16em] text-[var(--accent)] font-bold mb-0.5">
          <QrCode className="w-3 h-3" />
          <span>FIND YOUR PHOTOS</span>
        </div>
        <p className="font-[family-name:var(--font-display)] text-sm font-semibold text-[var(--text-primary)] leading-tight mb-1">
          Scan to match your selfie
        </p>
        <span className="text-[11px] text-[var(--text-secondary)] font-sans flex items-center gap-0.5">
          <span>No app required</span>
          <ArrowUpRight className="w-3 h-3 text-[var(--text-tertiary)]" />
        </span>
      </div>
    </aside>
  );
}

export function LiveStatusGlyph({
  publicCount,
  updatedAt,
  leaseHeld,
  className = "",
}: {
  publicCount: number | null;
  updatedAt: number | null;
  leaseHeld: boolean;
  className?: string;
}) {
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const agoSec = updatedAt ? Math.max(0, Math.round((nowTick - updatedAt) / 1000)) : null;

  return (
    <div
      className={`absolute bottom-[3%] right-[3%] z-30 font-mono tabular-nums text-xs px-4 py-2 rounded-full glass-card backdrop-blur-2xl bg-slate-950/80 border border-white/10 shadow-2xl flex items-center gap-2.5 text-[var(--text-primary)] transition-opacity duration-500 ${className}`}
    >
      <div className="flex items-center gap-1.5 text-[var(--emerald-live)] font-bold">
        <span className="live-dot" />
        <span>LIVE</span>
      </div>
      <span className="text-white/20">|</span>
      <span className="text-white font-semibold">
        {publicCount === null ? "—" : publicCount.toLocaleString()} photos
      </span>
      <span className="text-white/20">|</span>
      <span className="text-[var(--text-secondary)] hidden sm:inline">agent directed</span>
      <span className="text-white/20 hidden sm:inline">|</span>
      <span className="text-[var(--text-secondary)]">
        {agoSec === null ? "—" : `sync ${agoSec}s ago`}
      </span>
    </div>
  );
}

// Preserve backward-compatible MonogramAndQr alias
export function MonogramAndQr(props: {
  eventName: string;
  stageLabel?: string | null;
  joinUrl: string;
  qrSizePx?: number;
}) {
  return <DemoQrBadge joinUrl={props.joinUrl} qrSizePx={props.qrSizePx} />;
}
