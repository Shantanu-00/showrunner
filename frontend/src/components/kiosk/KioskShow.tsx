"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { WifiOff, Sparkles } from "lucide-react";
import type { EventPublicInfo, KioskPlaylist } from "@/lib/types";
import { countPublicIndexed, listenKioskPlaylist } from "@/lib/firestore";
import { joinUrl, slotHoldSec } from "@/lib/kiosk";
import { dayLabelFromIndex } from "@/lib/eventTime";
import { useAccessMode } from "@/lib/eventAccess";
import { useSlotPrefetch } from "@/lib/kioskPrefetch";
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

/** How often to re-ask whether the next slide has landed, once the current hold has expired, and how
 * long to keep asking. The wait is bounded because the alternative to advancing is stretching the
 * current photograph, and a wall that never moves is a worse failure than one brief shimmer. */
const NEXT_SLIDE_POLL_MS = 250;
const NEXT_SLIDE_MAX_WAIT_MS = 3_000;

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

  const shown = playlist ?? (!connected ? cachedPlaylist.current : null);
  const slots = shown?.slots ?? [];

  // Warm the next ~10 seconds of the programme while this slide is on screen, and find out what is
  // ready. The publisher's *decisions* were always ~5 minutes ahead; without this the *pixels* were
  // fetched only once a slide was already rendering, so every transition paid an API → 302 → GCS round
  // trip in front of the room. Must mirror `MediaImg`'s own auth choice — hence `useAccessMode`, not a
  // guess (`lib/kioskPrefetch.ts`).
  const accessMode = useAccessMode(eventId);
  const { currentReady, isReady } = useSlotPrefetch(
    eventId,
    slots,
    slotIndex,
    accessMode === undefined ? undefined : accessMode !== "open",
    shown?.revision
  );

  const revision = playlist?.revision;
  const slotCount = playlist?.slots?.length ?? 0;

  /* The advance, gated twice.
   *
   * **The hold does not begin until this slide is actually up.** It used to start the moment the index
   * changed, so a slide whose bytes took two seconds to land got two seconds less time on the wall —
   * and slot 0, the one a room is watching from cold, got the worst of it.
   *
   * **And it will not walk into a slide that has nothing to show.** When the hold expires, the *next*
   * slot must be ready; if it is not, this re-checks every 250 ms rather than advancing, so the room
   * sees the current photograph a moment longer instead of a shimmer. `READY_TIMEOUT_MS` inside the
   * prefetch hook is the other half of that promise: a photograph that genuinely cannot be fetched (a
   * deleted object, a bucket 403) counts as ready, so a broken slide can never freeze the programme. */
  useEffect(() => {
    if (slotCount === 0 || !currentReady) return;
    const slot = (playlist?.slots ?? [])[slotIndex % slotCount];
    if (!slot) return;
    // A reel owns its own timing (`slotHoldSec` returns 0) — `ReelSlot`'s own `onEnded` is what ends
    // it, and the `|| 8` fallback here is what covers a premiere that never fires one.
    const holdMs = (slotHoldSec(slot) || 8) * 1000;

    let cancelled = false;
    let waited = 0;
    const advance = () => {
      if (cancelled) return;
      if (isReady(slotIndex + 1) || waited >= NEXT_SLIDE_MAX_WAIT_MS) {
        setSlotIndex((i) => i + 1);
        return;
      }
      waited += NEXT_SLIDE_POLL_MS;
      timer = setTimeout(advance, NEXT_SLIDE_POLL_MS);
    };
    let timer = setTimeout(advance, holdMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, slotCount, slotIndex, currentReady]);

  // Hold the previous slide on screen while the new one warms, so a transition is photo → photo and
  // never photo → shimmer → photo. `currentReady` is what un-holds it.
  //
  // `hasPainted` is the *first* paint's half of the same promise, and it is the case that matters most:
  // before it, there is no previous slide to hold, so falling through to `slots[0]` would open the
  // evening on exactly the shimmer this path exists to remove. Until slot 0 reports ready the wall shows
  // the "Standing By" card below — a designed state rather than a loading state — and the prefetch hook's
  // own `READY_TIMEOUT_MS` guarantees that is never where the wall stops.
  const activeIndex = slots.length > 0 ? slotIndex % slots.length : 0;
  const paintedIndex = useRef(activeIndex);
  const hasPainted = useRef(false);
  if (currentReady) {
    paintedIndex.current = activeIndex;
    hasPainted.current = true;
  }
  const activeSlot =
    slots.length > 0 && hasPainted.current ? slots[paintedIndex.current % slots.length] ?? null : null;

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
      {/* Theater Frame & Active Slot Media. Keyed on the *painted* index, not the logical one: while a
          new slide warms, the logical index has already moved and the key must not follow it, or the
          previous photograph would unmount and leave the shimmer this whole path exists to remove. */}
      {activeSlot ? (
        <SlotRenderer key={paintedIndex.current} eventId={eventId} slot={activeSlot} />
      ) : (
        /* Two different silences, one card — and deliberately **no shimmer and no pulse** in either.
         * A skeleton is a promise that something specific is arriving in a moment; on a five-metre
         * screen in a room full of people it reads as breakage. The sub-line is what distinguishes
         * "this event has nothing on the wall yet" from "the first slide is warming", which lasts at
         * most `READY_TIMEOUT_MS`. */
        <div className="absolute inset-0 flex flex-col items-center justify-center animate-spring-in">
          <div className="w-24 h-24 rounded-full mb-6 flex items-center justify-center border border-white/10 bg-white/[0.03] shadow-2xl">
            <Sparkles className="w-10 h-10 text-[var(--accent)]" />
          </div>
          <p className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl text-[var(--text-primary)] font-semibold mb-2">
            {slots.length > 0 ? "Curtain Up" : "The Showcase is Standing By"}
          </p>
          <p className="text-xs text-[var(--text-secondary)] font-mono tracking-widest uppercase">
            {slots.length > 0 ? "Cueing the first frame" : "Autonomous Event Director Ready"}
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
