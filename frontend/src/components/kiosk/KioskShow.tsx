"use client";

import { useEffect, useRef, useState } from "react";
import type { EventPublicInfo, KioskPlaylist } from "@/lib/types";
import { countPublicIndexed, listenKioskPlaylist } from "@/lib/firestore";
import { joinUrl, slotHoldSec } from "@/lib/kiosk";
import { SlotRenderer } from "./SlotRenderer";
import { MonogramAndQr, LiveStatusGlyph } from "./Overlays";

/** The fullscreen show (spec 04 §4, spec 12 §6). A dumb client: it only ever renders what the
 * publisher already decided in `kiosk/playlist`, crossfading between the slots it lists. */
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
        setSlotIndex(0);
      },
      () => setConnected(false)
    );
  }, [eventId]);

  useEffect(() => {
    if (!playlist) return;
    void countPublicIndexed(eventId).then(setPublicCount, () => {});
  }, [eventId, playlist?.revision]);

  useEffect(() => {
    const slots = playlist?.slots ?? [];
    if (slots.length === 0) return;
    const slot = slots[slotIndex % slots.length];
    const holdMs = (slotHoldSec(slot) || 8) * 1000;
    const t = setTimeout(() => setSlotIndex((i) => i + 1), holdMs);
    return () => clearTimeout(t);
  }, [playlist, slotIndex]);

  const shown = playlist ?? (!connected ? cachedPlaylist.current : null);
  const slots = shown?.slots ?? [];
  const activeSlot = slots.length > 0 ? slots[slotIndex % slots.length] : null;

  const stageLabel = eventInfo?.stages?.find((s) => s.stageId === shown?.activeStageId)?.label;

  // Spec 04 §4 acceptance: stage change re-themes the kiosk ≤5s — driven live by the playlist
  // itself (the publisher flushes slots on stage change), not by the one-shot event bootstrap.
  useEffect(() => {
    if (shown?.activeStageId) document.documentElement.dataset.stage = shown.activeStageId;
  }, [shown?.activeStageId]);

  return (
    <div className="fixed inset-0 overflow-hidden" style={{ background: "var(--bg-0)" }}>
      {activeSlot ? (
        <SlotRenderer key={slotIndex} eventId={eventId} slot={activeSlot} />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="w-32 h-32 rounded-full skeleton-shimmer mb-6" />
          <p className="text-lg" style={{ color: "var(--ink-muted)" }}>
            The show is about to begin
          </p>
        </div>
      )}

      {/* `bounty_call` already renders its own enlarged join-QR (spec 12 §6) — showing the small
          permanent one too would put two QRs on screen; `reel` is a full-bleed cinematic
          takeover with no room in the frame for a corner overlay. */}
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
          className="absolute top-[3%] left-1/2 -translate-x-1/2 z-30 text-sm px-4 py-2 rounded-[var(--radius-pill)]"
          style={{ background: "var(--bg-glass)", color: "var(--warn)", border: "var(--hairline)" }}
        >
          📶 reconnecting — looping the last show
        </div>
      )}
    </div>
  );
}
