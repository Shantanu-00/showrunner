"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { WifiOff, Sparkles } from "lucide-react";
import type { EventPublicInfo, KioskPlaylist } from "@/lib/types";
import { countPublicIndexed, listenKioskPlaylist } from "@/lib/firestore";
import { joinUrl, slotHoldSec } from "@/lib/kiosk";
import { SlotRenderer } from "./SlotRenderer";
import { LiveBroadcastHeader, DemoQrBadge, LiveStatusGlyph } from "./Overlays";

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
  const [controlsVisible, setControlsVisible] = useState(true);
  const cachedPlaylist = useRef<KioskPlaylist | null>(null);
  const hideTimerRef = useRef<NodeJS.Timeout | null>(null);
  const lastLeadKey = useRef<string | null>(null);

  // Auto-hiding controls: reveal on mouse/touch interaction, fade out after 3.5s of inactivity
  const showControls = useCallback(() => {
    setControlsVisible(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => {
      setControlsVisible(false);
    }, 3500);
  }, []);

  useEffect(() => {
    showControls();
    const onActivity = () => showControls();
    window.addEventListener("mousemove", onActivity);
    window.addEventListener("touchstart", onActivity);
    window.addEventListener("keydown", onActivity);
    return () => {
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
      window.removeEventListener("mousemove", onActivity);
      window.removeEventListener("touchstart", onActivity);
      window.removeEventListener("keydown", onActivity);
    };
  }, [showControls]);

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
    <div
      className="fixed inset-0 overflow-hidden select-none bg-[var(--canvas-primary)] cursor-default"
      onClick={showControls}
    >
      {/* Theater Frame & Active Slot Media */}
      {activeSlot ? (
        <SlotRenderer key={slotIndex} eventId={eventId} slot={activeSlot} />
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center animate-spring-in">
          <div className="w-24 h-24 rounded-full skeleton-shimmer mb-6 flex items-center justify-center border border-white/10 shadow-2xl">
            <Sparkles className="w-10 h-10 text-[var(--accent)] animate-pulse" />
          </div>
          <p className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl text-[var(--text-primary)] font-semibold mb-2">
            The Showcase is Standing By
          </p>
          <p className="text-xs text-[var(--text-secondary)] font-mono tracking-widest uppercase">
            Autonomous Event Director Ready
          </p>
        </div>
      )}

      {/* Live Broadcast Header (Top Overlay with Animated Emerald LIVE SHOWCASE badge) */}
      <LiveBroadcastHeader
        eventName={eventInfo?.name ?? "Showrunner Showcase"}
        stageLabel={stageLabel}
        className={controlsVisible ? "opacity-100" : "opacity-0"}
      />

      {/* Demo QR Badge (Bottom Left Corner with "Scan to find your photos") */}
      {activeSlot?.type !== "bounty_call" && (
        <DemoQrBadge
          joinUrl={joinUrl(eventId)}
          qrSizePx={100}
          className={controlsVisible ? "opacity-100" : "opacity-0"}
        />
      )}

      {/* Live Status Glyph (Bottom Right Corner with Tabular Photo Count & Sync) */}
      <LiveStatusGlyph
        publicCount={publicCount}
        updatedAt={updatedAt}
        leaseHeld={connected && Boolean(shown)}
        className={controlsVisible ? "opacity-100" : "opacity-0"}
      />

      {/* Reconnecting Alert */}
      {!connected && (
        <div
          className="absolute top-[8%] left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 text-xs px-5 py-2.5 rounded-full glass-card border border-amber-500/40 text-amber-300 shadow-2xl backdrop-blur-xl bg-slate-950/90 font-mono"
        >
          <WifiOff className="w-4 h-4 animate-pulse text-amber-400" />
          <span>Reconnecting to Control Plane — Streaming Cached Sequence</span>
        </div>
      )}
    </div>
  );
}
