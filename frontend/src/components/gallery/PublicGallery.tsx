"use client";

import { useEffect, useMemo, useState } from "react";
import { Sparkles, Clock, WifiOff, HelpCircle, Eye, Play, Image as ImageIcon, Video as VideoIcon, X, ChevronDown } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { listenHighlights, listenPeopleTiers, listenPublicGallery } from "@/lib/firestore";
import { rankHighlights, whyFactorsForGallery } from "@/lib/scoring";
import { MediaImg } from "@/lib/MediaImg";
import { useHaptics } from "@/lib/useHaptics";
import { Lightbox } from "./Lightbox";
import { WhyThisPhoto } from "./WhyThisPhoto";

export type MediaFilterType = "all" | "photos" | "highlights" | "videos";

/** How many tiles render before the guest asks for more.
 *
 * The listener still takes the same 60 documents — it is one subscription either way — but *painting*
 * sixty photographs on a phone the moment the tab opens is what made the gallery feel slow, and most
 * of them were below the fold. A screenful arrives, the rest on request. Together with `loading="lazy"`
 * and `lib/mediaUrls.ts`'s byte cache, a return visit costs no image traffic at all. */
const PAGE_SIZE = 12;

function formatDuration(sec?: number | null): string {
  if (!sec) return "0:15";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function PublicGallery({
  eventId,
  stages,
  activeStageId,
  explainMode,
  /** Set from the timeline sheet, which is the only place a guest picks a moment now. The row of day
   * and phase chips that used to sit here took half a phone screen before a single photograph — on a
   * five-day trip it was two wrapped lines of buttons above an empty grid. */
  stageFilter = null,
  onClearStageFilter,
}: {
  eventId: string;
  stages: Array<{ stageId: string; label: string; day?: number | null }>;
  activeStageId?: string | null;
  explainMode: boolean;
  stageFilter?: string | null;
  onClearStageFilter?: () => void;
}) {
  const [mode, setMode] = useState<"recent" | "highlights">("recent");
  const [mediaFilter, setMediaFilter] = useState<MediaFilterType>("all");
  const [recent, setRecent] = useState<MediaDoc[]>([]);
  const [highlights, setHighlights] = useState<MediaDoc[]>([]);
  const [tierByPersonId, setTierByPersonId] = useState<Record<string, number>>({});
  const [streamDown, setStreamDown] = useState<null | "network" | "denied">(null);
  const [shown, setShown] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<MediaDoc | null>(null);
  const [showWhy, setShowWhy] = useState(false);
  const { tapHaptic } = useHaptics();

  useEffect(() => {
    const unsubRecent = listenPublicGallery(
      eventId,
      (items) => {
        setStreamDown(null);
        setRecent(items);
      },
      // Only the main stream drives the warning. Highlights needs its own composite index and its own
      // `curator.isHighlight` filter, so it can fail on its own — and when it did, the whole tab
      // claimed to be disconnected while the photographs beside the message were arriving live.
      //
      // The two failures also need different words. "Reconnecting…" over an empty grid was shown for
      // both, and for the common one it was simply untrue: a device whose token carries no membership
      // for this event is not reconnecting to anything, and no amount of waiting will change it.
      (err: Error & { code?: string }) =>
        setStreamDown(err?.code === "permission-denied" ? "denied" : "network")
    );
    const unsubHighlights = listenHighlights(eventId, setHighlights, () => setHighlights([]));
    const unsubTiers = listenPeopleTiers(eventId, setTierByPersonId, () => {});
    return () => {
      unsubRecent();
      unsubHighlights();
      unsubTiers();
    };
  }, [eventId]);

  const ranked = useMemo(
    () => (mode === "highlights" ? rankHighlights(highlights, tierByPersonId) : recent),
    [mode, highlights, recent, tierByPersonId]
  );

  const filteredByStage = useMemo(() => {
    if (!stageFilter) return ranked;
    if (stageFilter.startsWith("day:")) {
      const dayNum = Number(stageFilter.slice(4));
      const dayStageIds = new Set(stages.filter((s) => s.day === dayNum).map((s) => s.stageId));
      return ranked.filter((m) => m.curator?.stageId && dayStageIds.has(m.curator.stageId));
    }
    return ranked.filter((m) => m.curator?.stageId === stageFilter);
  }, [ranked, stageFilter, stages]);

  // Filter by media type. "Photos" means *not a video* — it used to also exclude anything the Curator
  // had flagged as a highlight, so on an event where the director liked most of what it saw, tapping
  // "Photos" emptied the grid and said "Waiting for first moments" over a gallery full of them.
  const visible = useMemo(() => {
    return filteredByStage.filter((m) => {
      const isVideo = m.kind === "video" || Boolean(m.proxyUri) || Boolean(m.durationSec);
      if (mediaFilter === "photos") return !isVideo;
      if (mediaFilter === "highlights") return Boolean(m.curator?.isHighlight);
      if (mediaFilter === "videos") return isVideo;
      return true;
    });
  }, [filteredByStage, mediaFilter]);

  // A new view starts at the top of its own first page rather than inheriting how far the last one
  // was scrolled through.
  useEffect(() => {
    setShown(PAGE_SIZE);
  }, [mode, mediaFilter, stageFilter]);

  const page = visible.slice(0, shown);
  const rankOf = (mediaId: string) => visible.findIndex((m) => m.mediaId === mediaId);
  const stageLabel = stageFilter
    ? stageFilter.startsWith("day:")
      ? `Day ${stageFilter.slice(4)}`
      : stages.find((s) => s.stageId === stageFilter)?.label ?? "One moment"
    : null;

  return (
    <section className="pb-28">
      {streamDown === "network" && (
        <div className="flex items-center justify-center gap-2 text-center text-xs px-4 pb-3 text-amber-400 font-mono">
          <WifiOff className="w-3.5 h-3.5 animate-pulse" />
          <span>Reconnecting to the live stream…</span>
        </div>
      )}

      {streamDown === "denied" && (
        <div className="mx-4 mb-3 px-4 py-3 rounded-2xl bg-amber-400/10 border border-amber-400/30 text-xs text-amber-200 leading-relaxed">
          This device isn&rsquo;t on the guest list for this event yet, so the gallery can&rsquo;t load.
          Open the invite link the host shared (or re-enter the invite code) and it will fill in.
        </div>
      )}

      {/* Two controls, both about what a guest is looking *at*. Everything else that used to live on
          this bar (day chips, phase chips) now lives in the timeline sheet. */}
      <div className="flex items-center justify-between gap-2 px-4 pb-2">
        <div className="flex items-center p-1 rounded-full glass-pill bg-slate-950/70 border border-white/10 shadow-md">
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              setMode("highlights");
            }}
            className={`flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full transition-all duration-200 cursor-pointer active:scale-95 ${
              mode === "highlights"
                ? "bg-[var(--accent)] text-slate-950 font-bold shadow-[0_0_16px_-2px_var(--accent-glow)]"
                : "text-[var(--text-secondary)] hover:text-white"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Picks</span>
          </button>
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              setMode("recent");
            }}
            className={`flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full transition-all duration-200 cursor-pointer active:scale-95 ${
              mode === "recent"
                ? "bg-white/15 text-white font-semibold shadow-inner"
                : "text-[var(--text-secondary)] hover:text-white"
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>Latest</span>
          </button>
        </div>

        <div className="flex items-center p-1 rounded-full glass-pill bg-slate-950/70 border border-white/10 shadow-sm">
          {(
            [
              { id: "all", label: "All" },
              { id: "photos", label: "Photos", icon: ImageIcon },
              { id: "videos", label: "Videos", icon: VideoIcon },
            ] as Array<{ id: MediaFilterType; label: string; icon?: React.ElementType }>
          ).map((tab) => {
            const isActive = mediaFilter === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => {
                  tapHaptic();
                  setMediaFilter(tab.id);
                }}
                className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full transition-all duration-200 cursor-pointer active:scale-95 ${
                  isActive
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "text-[var(--text-secondary)] hover:text-white"
                }`}
                title={tab.label}
                aria-label={tab.label}
              >
                {Icon ? <Icon className="w-3.5 h-3.5" /> : <span>{tab.label}</span>}
                <span className="hidden sm:inline">{Icon ? tab.label : null}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* The one trace of a stage filter on this tab: what is being filtered, and how to stop. */}
      {stageFilter && (
        <div className="px-4 pb-2">
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              onClearStageFilter?.();
            }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[var(--accent)]/15 border border-[var(--accent)]/40 text-[var(--accent)] text-xs font-medium active:scale-95 transition-all"
          >
            <span>{stageLabel}</span>
            <X className="w-3.5 h-3.5" />
            <span className="sr-only">Show every moment again</span>
          </button>
        </div>
      )}

      {visible.length === 0 ? (
        <div className="text-center mt-8 px-6 py-12 rounded-3xl glass-card mx-4 border border-dashed border-white/10 shadow-2xl animate-spring-in">
          <div className="w-12 h-12 rounded-full bg-[var(--accent)]/10 flex items-center justify-center text-[var(--accent)] mx-auto mb-3 shadow-inner">
            <Sparkles className="w-5 h-5" />
          </div>
          <p className="font-semibold text-base text-[var(--text-primary)] mb-1">
            {stageFilter || mediaFilter !== "all" ? "Nothing here yet" : "Waiting for first moments"}
          </p>
          <p className="text-xs text-[var(--text-secondary)] max-w-sm mx-auto leading-relaxed">
            {stageFilter || mediaFilter !== "all"
              ? "Nothing matches this filter yet. Clear it to see everything the event has so far."
              : "Tap the camera button to send a photo. The director curates and projects highlights in real time."}
          </p>
        </div>
      ) : (
        <>
          <div className="columns-2 sm:columns-3 lg:columns-4 gap-3 px-3 mt-1 [column-fill:_balance]">
            {page.map((media, index) => {
              const isVideo = media.kind === "video" || Boolean(media.proxyUri) || Boolean(media.durationSec);
              const isHighlight = media.curator?.isHighlight;

              return (
                <button
                  key={media.mediaId}
                  type="button"
                  onClick={() => {
                    tapHaptic();
                    setSelected(media);
                  }}
                  className="group relative block w-full mb-3 break-inside-avoid rounded-2xl overflow-hidden glass-card border border-white/10 hover:border-[var(--accent)]/60 transition-all duration-300 transform hover:-translate-y-1 active:scale-[0.98] shadow-lg cursor-pointer animate-spring-in"
                  style={{ animationDelay: `${Math.min(index * 30, 240)}ms` }}
                >
                  {media.thumbUri ? (
                    <MediaImg
                      eventId={eventId}
                      mediaId={media.mediaId}
                      variant="thumb"
                      alt={media.curator?.caption ?? ""}
                      className="w-full h-auto block object-cover group-hover:scale-105 transition-transform duration-400 ease-out"
                      fallback={<div className="w-full aspect-[4/5] bg-gradient-to-br from-slate-900/90 via-slate-800/60 to-slate-950 border border-white/5 animate-pulse rounded-2xl" />}
                    />
                  ) : (
                    <div className="w-full aspect-[4/5] bg-gradient-to-br from-slate-900/90 via-slate-800/60 to-slate-950 flex items-center justify-center rounded-2xl border border-white/5">
                      <ImageIcon className="w-6 h-6 text-white/20" />
                    </div>
                  )}

                  {/* Video Duration Pill & Optical Play Icon — only for actual video content */}
                  {isVideo && (
                    <div className="absolute top-2.5 right-2.5 z-10">
                      <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-mono tabular-nums font-semibold bg-slate-950/80 backdrop-blur-md text-white border border-white/15 shadow-md">
                        <Play className="w-2.5 h-2.5 fill-current optical-play-icon text-[var(--accent)]" />
                        <span>{formatDuration(media.durationSec)}</span>
                      </span>
                    </div>
                  )}

                  {/* Highlight badge — this is a still photo the Curator flagged, never a video */}
                  {!isVideo && isHighlight && (
                    <div className="absolute top-2.5 right-2.5 z-10">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono uppercase tracking-wider font-semibold bg-slate-950/80 backdrop-blur-md text-[var(--accent)] border border-[var(--accent)]/30 shadow-md">
                        <Sparkles className="w-2.5 h-2.5" />
                        <span>Highlight</span>
                      </span>
                    </div>
                  )}

                  {/* Center Play Glyph on Video cards */}
                  {isVideo && (
                    <div className="absolute inset-0 flex items-center justify-center opacity-75 group-hover:opacity-100 transition-opacity">
                      <div className="w-11 h-11 rounded-full bg-slate-950/60 backdrop-blur-md flex items-center justify-center text-white border border-white/20 shadow-xl group-hover:scale-110 transition-transform">
                        <Play className="w-5 h-5 fill-current optical-play-icon text-white" />
                      </div>
                    </div>
                  )}

                  {/* Hover overlay with caption */}
                  <div className="absolute inset-0 bg-gradient-to-t from-slate-950/90 via-slate-950/30 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex items-end p-3">
                    <span className="text-[11px] text-white font-medium truncate flex items-center gap-1.5">
                      <Eye className="w-3.5 h-3.5 text-[var(--accent)]" />
                      <span>{media.curator?.caption || "View moment"}</span>
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {visible.length > page.length && (
            <div className="px-4 mt-2 flex justify-center">
              <button
                type="button"
                onClick={() => {
                  tapHaptic();
                  setShown((n) => n + PAGE_SIZE);
                }}
                className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-full glass-card border border-white/10 hover:border-[var(--accent)]/50 text-xs font-medium text-[var(--text-secondary)] hover:text-white active:scale-95 transition-all"
              >
                <ChevronDown className="w-3.5 h-3.5" />
                <span>
                  Show more · {visible.length - page.length} left
                </span>
              </button>
            </div>
          )}
        </>
      )}

      {selected && (
        <Lightbox
          eventId={eventId}
          media={selected}
          onClose={() => {
            setSelected(null);
            setShowWhy(false);
          }}
          actions={
            explainMode ? (
              <button
                type="button"
                onClick={() => setShowWhy(true)}
                className="flex items-center gap-1.5 text-xs px-4 py-2.5 rounded-full glass-card hover:border-[var(--accent)] text-[var(--accent)] font-medium active:scale-95 transition-all cursor-pointer shadow-lg"
              >
                <HelpCircle className="w-3.5 h-3.5" />
                <span>Explain Ranking Factors</span>
              </button>
            ) : undefined
          }
        />
      )}

      {selected && showWhy && explainMode && (
        <WhyThisPhoto
          factors={whyFactorsForGallery(
            selected,
            tierByPersonId,
            rankOf(selected.mediaId),
            stages,
            activeStageId
          )}
          rankLabel={mode === "highlights" ? "Director's Picks Rank" : "Position, newest first"}
          rankNote={
            mode === "highlights"
              ? "Ordered by aesthetic score × subject weight — the same re-rank every viewer sees."
              : "This tab is ordered by capture time. The factors above are what the wall would score it on."
          }
          onClose={() => setShowWhy(false)}
        />
      )}
    </section>
  );
}
