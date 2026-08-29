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

  // The dependency list here is load-bearing and used to be `[playlist, slotIndex]`, which froze the
  // show completely.
  //
  // `playlist` is a fresh object on **every** Firestore snapshot, including one where nothing the
  // viewer can see has changed: the publisher deliberately touches `checkedAt` on a rebuild whose
  // program fingerprint is unchanged (spec 04 §4 / HANDOFF §4.21). Every such snapshot re-ran this
  // effect, whose cleanup cancels the pending advance and starts a new full-length timer. On the demo
  // event the director ticks every 30 s and nudges the publisher each time, so `checkedAt` moved more
  // often than a slot's own hold (6 s for a hero, 10 s for a bounty takeover) — the timeout was
  // cancelled before it could ever fire and the wall parked on slot 0 for ever. Observed live: 60 s on
  // one bounty poster, zero hero photographs, zero `/render` requests.
  //
  // So depend on the *decisions* rather than the object: a new revision genuinely changes the program
  // and should retime, a `checkedAt` touch must not. Same discipline as the publisher's own
  // fingerprint — one side detects changes so the other need not re-render, and both halves have to
  // agree on what counts as a change.
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
