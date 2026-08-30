"use client";

import { useEffect, useRef, useState } from "react";
import { WifiOff, Sparkles } from "lucide-react";
import type { EventPublicInfo, KioskPlaylist } from "@/lib/types";
import { countPublicIndexed, listenKioskPlaylist } from "@/lib/firestore";
import { joinUrl, slotHoldSec } from "@/lib/kiosk";
import { SlotRenderer } from "./SlotRenderer";
import { MonogramAndQr, LiveStatusGlyph } from "./Overlays";

export function KioskShow({
  eventId,
  eventInfo,
}: {
  eventId: string;
  eventInfo: EventPublicInfo | null;
}) {
  const [playlist, setPlaylist] = useState<KioskPlaylist | null>(null);
  const [connected, setConnected] = useState(true);
  const [slotIndex, setSlotIndex] = useState(0);
  const [publicCount, setPublicCount] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const cachedPlaylist = useRef<KioskPlaylist | null>(null);
  // B1: the show used to rewind to slot 0 on every playlist snapshot, so a busy event's tail hero
  // slots could go unreached forever. Reset now tracks `leadKey` — the publisher only sets it for an
  // actual interrupt (a reel premiere or a bounty takeover) — so a tail-only rebuild (a new photo
  // entering the pool, recency reordering) leaves the viewer's position alone.
  const lastLeadKey = useRef<string | null>(null);

  useEffect(() => {
    return listenKioskPlaylist(
      eventId,
      (pl) => {
        setConnected(true);
        if (pl) {
          cachedPlaylist.current = pl;
          setUpdatedAt(Date.now());
        }
        setPlaylist(pl);
        const leadKey = pl?.leadKey ?? null;
        if (leadKey !== null && leadKey !== lastLeadKey.current) {
          setSlotIndex(0);
        }
        lastLeadKey.current = leadKey;
      },
      () => setConnected(false)
    );
  }, [eventId]);

  useEffect(() => {
    if (!playlist) return;
    void countPublicIndexed(eventId).then(setPublicCount, () => {});
  }, [eventId, playlist?.revision]);

  const revision = playlist?.revision;
  const slotCount = playlist?.slots?.length ?? 0;
  useEffect(() => {
    if (slotCount === 0) return;
    const slot = (playlist?.slots ?? [])[slotIndex % slotCount];
    if (!slot) return;
    const holdMs = (slotHoldSec(slot) || 8) * 1000;
    const t = setTimeout(() => setSlotIndex((i) => i + 1), holdMs);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, slotCount, slotIndex]);

  const shown = playlist ?? (!connected ? cachedPlaylist.current : null);
  const slots = shown?.slots ?? [];
  const activeSlot = slots.length > 0 ? slots[slotIndex % slots.length] : null;

  const stageLabel = eventInfo?.stages?.find((s) => s.stageId === shown?.activeStageId)?.label;

  useEffect(() => {
    if (shown?.activeStageId) document.documentElement.dataset.stage = shown.activeStageId;
  }, [shown?.activeStageId]);

  return (
    <div className="fixed inset-0 overflow-hidden select-none" style={{ background: "var(--bg-0)" }}>
      {activeSlot ? (
        <SlotRenderer key={slotIndex} eventId={eventId} slot={activeSlot} />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="w-24 h-24 rounded-full skeleton-shimmer mb-6 flex items-center justify-center border border-white/10">
            <Sparkles className="w-10 h-10 text-[var(--accent)] animate-pulse" />
          </div>
          <p className="font-[family-name:var(--font-display)] text-2xl text-[var(--ivory)] mb-1">
            The Show is About to Begin
          </p>
          <p className="text-xs text-[var(--ink-muted)] font-mono">
            Autonomous Media Director Standing By
          </p>
        </div>
      )}

      {activeSlot?.type !== "bounty_call" && activeSlot?.type !== "reel" && (
        <MonogramAndQr
          eventName={eventInfo?.name ?? "Showrunner"}
          stageLabel={stageLabel}
          joinUrl={joinUrl(eventId)}
        />
      )}
      <LiveStatusGlyph publicCount={publicCount} updatedAt={updatedAt} leaseHeld={connected && Boolean(shown)} />

      {!connected && (
        <div
          className="absolute top-[3%] left-1/2 -translate-x-1/2 z-30 flex items-center gap-2 text-xs px-4 py-2 rounded-full glass-card border border-[var(--warn)]/40 text-[var(--warn)] shadow-xl"
        >
          <WifiOff className="w-3.5 h-3.5" />
          <span>Reconnecting to Control Plane — Looping Cached Stream</span>
        </div>
      )}
    </div>
  );
}
