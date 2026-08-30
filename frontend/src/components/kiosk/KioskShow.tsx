"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { WifiOff, Sparkles } from "lucide-react";
import type { EventPublicInfo, KioskPlaylist } from "@/lib/types";
import { countPublicIndexed, listenKioskPlaylist } from "@/lib/firestore";
import { joinUrl, slotHoldSec } from "@/lib/kiosk";
import { dayLabelFromIndex } from "@/lib/eventTime";
import { SlotRenderer } from "./SlotRenderer";
import { LiveBroadcastHeader, DemoQrBadge, LiveStatusGlyph } from "./Overlays";

/** Old seeded events wrote the kiosk playlist's `theme` field with wedding-stage names; new
 * events (and the host wizard's stage editor) write one of the 8 palette names directly (see
 * `tokens.css`'s `[data-stage-theme]` blocks). This map is the only place that legacy naming is
 * known — everything downstream only ever sees the 8 new names. */
const LEGACY_STAGE_THEME_MAP: Record<string, string> = {
  turmeric: "gold",
  night: "violet",
  dawn: "sunrise",
};
const STAGE_THEMES = new Set([
  "gold",
  "violet",
  "crimson",
  "ocean",
  "forest",
  "neon",
  "slate",
  "sunrise",
]);

function resolveStageTheme(theme: string | null | undefined): string | null {
  if (!theme) return null;
  const mapped = LEGACY_STAGE_THEME_MAP[theme] ?? theme;
  return STAGE_THEMES.has(mapped) ? mapped : null;
}

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

  const activeStageInfo = eventInfo?.stages?.find((s) => s.stageId === shown?.activeStageId);
  const activeDayLabel = dayLabelFromIndex(activeStageInfo?.day);
  const stageLabel = activeStageInfo
    ? activeDayLabel
      ? `${activeDayLabel} — ${activeStageInfo.label}`
      : activeStageInfo.label
    : undefined;

  // Spec 04 §4 acceptance: stage change re-themes the kiosk ≤5s — driven live by the playlist
  // itself (the publisher flushes slots on stage change), not by the one-shot event bootstrap.
  // `data-stage-theme` (mapped from the playlist's free-string `theme`, legacy names included —
  // see the map above) is what `tokens.css` actually retunes on; `data-stage` is kept too since
  // other surfaces read the raw stage id.
  useEffect(() => {
    if (shown?.activeStageId) document.documentElement.dataset.stage = shown.activeStageId;
    const stageTheme = resolveStageTheme(shown?.theme);
    if (stageTheme) {
      document.documentElement.dataset.stageTheme = stageTheme;
    } else {
      delete document.documentElement.dataset.stageTheme;
    }
  }, [shown?.activeStageId, shown?.theme]);

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
