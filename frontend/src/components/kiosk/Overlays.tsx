"use client";

import { useEffect, useState } from "react";
import { JoinQr } from "./JoinQr";

/** The three permanent kiosk overlays (spec 12 §6): monogram + stage + join-QR bottom-left, and
 * the truthful live status glyph bottom-right. Every number here traces to a real value —
 * no placeholder counters. */
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
    <div className="absolute bottom-[3%] left-[3%] z-30 flex items-end gap-4">
      <JoinQr url={joinUrl} sizePx={qrSizePx} />
      <div>
        <p className="font-[family-name:var(--font-display)] text-2xl" style={{ color: "var(--ivory)" }}>
          {eventName}
        </p>
        {stageLabel && (
          <p className="text-sm font-medium" style={{ color: "var(--accent)" }}>
            {stageLabel}
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
      className="absolute bottom-[3%] right-[3%] z-30 font-mono tabular-nums text-sm flex items-center gap-2"
      style={{ color: "var(--ivory)" }}
    >
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full"
        style={{
          background: "var(--ok)",
          opacity: leaseHeld ? 1 : 0.3,
          animation: leaseHeld ? "gold-sheen 2s ease-in-out infinite" : "none",
        }}
      />
      <span>LIVE</span>
      <span aria-hidden>·</span>
      <span>{publicCount === null ? "—" : publicCount.toLocaleString()} photos</span>
      <span aria-hidden>·</span>
      <span>directed by agents</span>
      <span aria-hidden>·</span>
      <span>{agoSec === null ? "—" : `updated ${agoSec}s ago`}</span>
    </div>
  );
}
