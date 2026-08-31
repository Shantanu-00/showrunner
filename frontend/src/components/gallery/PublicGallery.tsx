"use client";

import { useEffect, useMemo, useState } from "react";
import { Sparkles, Clock, WifiOff, HelpCircle, Eye, Play, Image as ImageIcon, Video as VideoIcon } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { listenHighlights, listenPeopleTiers, listenPublicGallery } from "@/lib/firestore";
import { rankHighlights, whyFactorsForGallery } from "@/lib/scoring";
import { MediaImg } from "@/lib/MediaImg";
import { useHaptics } from "@/lib/useHaptics";
import { StageChips } from "./StageChips";
import { Lightbox } from "./Lightbox";
import { WhyThisPhoto } from "./WhyThisPhoto";

export type MediaFilterType = "all" | "photos" | "highlights" | "videos";

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
}: {
  eventId: string;
  stages: Array<{ stageId: string; label: string; day?: number | null }>;
  activeStageId?: string | null;
  explainMode: boolean;
}) {
  const [mode, setMode] = useState<"recent" | "highlights">("recent");
  const [mediaFilter, setMediaFilter] = useState<MediaFilterType>("all");
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const [recent, setRecent] = useState<MediaDoc[]>([]);
  const [highlights, setHighlights] = useState<MediaDoc[]>([]);
  const [tierByPersonId, setTierByPersonId] = useState<Record<string, number>>({});
  const [connected, setConnected] = useState(true);
  const [selected, setSelected] = useState<MediaDoc | null>(null);
  const [showWhy, setShowWhy] = useState(false);
  const { tapHaptic } = useHaptics();

  useEffect(() => {
    const onError = () => setConnected(false);
    const unsubRecent = listenPublicGallery(eventId, (items) => {
      setConnected(true);
      setRecent(items);
    }, onError);
    const unsubHighlights = listenHighlights(eventId, (items) => {
      setConnected(true);
      setHighlights(items);
    }, onError);
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

  const filteredByStage = useMemo(
    () => (stageFilter ? ranked.filter((m) => m.curator?.stageId === stageFilter) : ranked),
    [ranked, stageFilter]
  );

  // Filter by media type: All / Photos / Highlights / Videos. "Highlights" is the Curator's
  // isHighlight flag on an ordinary photo — not the generated-video reels (those only ever play on
  // the kiosk); labeling it "Reels" here promised a video that this tab never had.
  const visible = useMemo(() => {
    return filteredByStage.filter((m) => {
      const isVideo = m.kind === "video" || Boolean(m.proxyUri) || Boolean(m.durationSec);
      const isHighlight = m.curator?.isHighlight || false;

      if (mediaFilter === "all") return true;
      if (mediaFilter === "photos") return !isVideo && !isHighlight;
      if (mediaFilter === "highlights") return isHighlight;
      if (mediaFilter === "videos") return isVideo;
      return true;
    });
  }, [filteredByStage, mediaFilter]);

  const rankOf = (mediaId: string) => visible.findIndex((m) => m.mediaId === mediaId);

  return (
    <section className="pb-28">
      {!connected && (
        <div className="flex items-center justify-center gap-2 text-center text-xs px-4 pb-3 text-amber-400 font-mono">
          <WifiOff className="w-3.5 h-3.5 animate-pulse" />
          <span>Reconnecting to Live Control Plane…</span>
        </div>
      )}

      {/* Unified Streamlined Control Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2.5 px-4 pb-2.5">
        {/* Mode Segment: Curated Picks vs Latest */}
        <div className="flex items-center p-1 rounded-full glass-pill bg-white/5 border border-white/10 shadow-md">
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              setMode("highlights");
            }}
            className={`flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full transition-all duration-200 cursor-pointer active:scale-95 ${
              mode === "highlights"
                ? "bg-[var(--accent)] text-slate-950 font-bold shadow-md"
                : "text-[var(--text-secondary)] hover:text-white"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Curated Picks</span>
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
            <span>Latest Stream</span>
          </button>
        </div>

        {/* Compact Media Type Filter: All / Photos / Videos */}
        <div className="flex items-center p-0.5 rounded-full glass-pill bg-white/5 border border-white/10 shadow-sm">
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
                    ? "bg-white/15 text-white font-semibold"
                    : "text-[var(--text-secondary)] hover:text-white"
                }`}
                title={tab.label}
              >
                {Icon && <Icon className="w-3 h-3" />}
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Stage ceremony / multi-day timeline chips */}
      <StageChips stages={stages} active={stageFilter} onChange={setStageFilter} />

      {/* Masonry Grid Layout: 2 cols on mobile, 3-4 cols on laptop */}
      {visible.length === 0 ? (
        <div className="text-center mt-10 px-6 py-12 rounded-3xl glass-card mx-4 border border-dashed border-white/10 shadow-2xl animate-spring-in">
          <div className="w-12 h-12 rounded-full bg-[var(--accent)]/10 flex items-center justify-center text-[var(--accent)] mx-auto mb-3 shadow-inner">
            <Sparkles className="w-5 h-5" />
          </div>
          <p className="font-semibold text-base text-[var(--text-primary)] mb-1">
            Waiting for first moments
          </p>
          <p className="text-xs text-[var(--text-secondary)] max-w-sm mx-auto leading-relaxed">
            Scan the QR code or tap the Camera button to capture moments. The AI Director curates and projects highlights in real time.
          </p>
        </div>
      ) : (
        <div className="columns-2 sm:columns-3 lg:columns-4 gap-3 px-3 mt-2 [column-fill:_balance]">
          {visible.map((media, index) => {
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
                style={{ animationDelay: `${Math.min(index * 40, 320)}ms` }}
              >
                {media.thumbUri ? (
                  <MediaImg
                    eventId={eventId}
                    mediaId={media.mediaId}
                    variant="thumb"
                    alt={media.curator?.caption ?? ""}
                    className="w-full h-auto block object-cover group-hover:scale-105 transition-transform duration-400 ease-out"
                    fallback={<div className="w-full aspect-[4/5] bg-white/5 animate-pulse rounded-2xl" />}
                  />
                ) : (
                  <div className="w-full aspect-[4/5] bg-white/5 flex items-center justify-center rounded-2xl border border-white/5">
                    <ImageIcon className="w-6 h-6 text-white/20" />
                  </div>
                )}

                {/* Video Duration Pill & Optical Play Icon — only for actual video content */}
                {isVideo && (
                  <div className="absolute top-2.5 right-2.5 z-10">
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-mono tabular-nums font-semibold bg-slate-950/75 backdrop-blur-md text-white border border-white/15 shadow-md">
                      <Play className="w-2.5 h-2.5 fill-current optical-play-icon text-[var(--accent)]" />
                      <span>{formatDuration(media.durationSec)}</span>
                    </span>
                  </div>
                )}

                {/* Highlight badge — this is a still photo the Curator flagged, never a video */}
                {!isVideo && isHighlight && (
                  <div className="absolute top-2.5 right-2.5 z-10">
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-mono uppercase tracking-wider font-semibold bg-slate-950/75 backdrop-blur-md text-[var(--accent)] border border-white/15 shadow-md">
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

                {/* Aesthetic / Highlight badge */}
                {media.curator?.aestheticScore && media.curator.aestheticScore > 80 && (
                  <div className="absolute top-2.5 left-2.5 z-10 pointer-events-none">
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-mono uppercase tracking-wider font-bold bg-[var(--accent)] text-slate-950 shadow-md">
                      <Sparkles className="w-2.5 h-2.5" />
                      <span>Top Pick</span>
                    </span>
                  </div>
                )}

                {/* Hover overlay with caption */}
                <div className="absolute inset-0 bg-gradient-to-t from-slate-950/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex items-end p-3">
                  <span className="text-[11px] text-white font-medium truncate flex items-center gap-1.5">
                    <Eye className="w-3.5 h-3.5 text-[var(--accent)]" />
                    <span>{media.curator?.caption || "View moment"}</span>
                  </span>
                </div>
              </button>
            );
          })}
        </div>
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
