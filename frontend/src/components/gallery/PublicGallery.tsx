"use client";

import { useEffect, useMemo, useState } from "react";
import { Sparkles, Clock, WifiOff, HelpCircle, Eye } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { listenHighlights, listenPeopleTiers, listenPublicGallery } from "@/lib/firestore";
import { rankHighlights, whyFactorsForGallery } from "@/lib/scoring";
import { MediaImg } from "@/lib/MediaImg";
import { StageChips } from "./StageChips";
import { Lightbox } from "./Lightbox";
import { WhyThisPhoto } from "./WhyThisPhoto";

export function PublicGallery({
  eventId,
  stages,
  explainMode,
}: {
  eventId: string;
  stages: Array<{ stageId: string; label: string }>;
  explainMode: boolean;
}) {
  const [mode, setMode] = useState<"recent" | "highlights">("recent");
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const [recent, setRecent] = useState<MediaDoc[]>([]);
  const [highlights, setHighlights] = useState<MediaDoc[]>([]);
  const [tierByPersonId, setTierByPersonId] = useState<Record<string, number>>({});
  const [connected, setConnected] = useState(true);
  const [selected, setSelected] = useState<MediaDoc | null>(null);
  const [showWhy, setShowWhy] = useState(false);

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

  const visible = useMemo(
    () =>
      stageFilter ? ranked.filter((m) => m.curator?.stageId === stageFilter) : ranked,
    [ranked, stageFilter]
  );

  const rankOf = (mediaId: string) => visible.findIndex((m) => m.mediaId === mediaId) + 1;

  return (
    <section>
      {!connected && (
        <div className="flex items-center justify-center gap-1.5 text-center text-xs px-4 pb-3 text-[var(--warn)]">
          <WifiOff className="w-3.5 h-3.5" />
          <span>Reconnecting — the gallery will catch up live</span>
        </div>
      )}

      <div className="flex items-center gap-2 px-4 pb-3">
        <div className="flex items-center p-1 rounded-full bg-white/5 border border-white/10">
          <button
            type="button"
            onClick={() => setMode("recent")}
            className={`flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full transition-all font-medium ${
              mode === "recent"
                ? "bg-[var(--accent)] text-black font-semibold shadow-md"
                : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            <span>Latest</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("highlights")}
            className={`flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full transition-all font-medium ${
              mode === "highlights"
                ? "bg-[var(--accent)] text-black font-semibold shadow-md"
                : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Director&rsquo;s Picks</span>
          </button>
        </div>
      </div>

      <StageChips stages={stages} active={stageFilter} onChange={setStageFilter} />

      {visible.length === 0 ? (
        <div className="text-center mt-16 px-6 py-12 rounded-2xl glass-card mx-4 border border-dashed border-white/10">
          <div className="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center text-[var(--ink-muted)] mx-auto mb-3">
            <Sparkles className="w-6 h-6" />
          </div>
          <p className="font-[family-name:var(--font-display)] text-lg text-[var(--ivory)] mb-1">
            Waiting for first uploads
          </p>
          <p className="text-xs text-[var(--ink-muted)] max-w-sm mx-auto">
            Scan the QR code or tap the Camera tab to capture moments and send them to the autonomous director.
          </p>
        </div>
      ) : (
        <div className="columns-2 sm:columns-3 gap-2.5 px-3 mt-3 [column-fill:_balance]">
          {visible.map((media) => (
            <button
              key={media.mediaId}
              type="button"
              onClick={() => setSelected(media)}
              className="group relative block w-full mb-2.5 break-inside-avoid rounded-xl overflow-hidden glass-card hover:border-[var(--accent)] transition-all transform hover:-translate-y-0.5 shadow-md"
            >
              {media.thumbUri ? (
                // On an invite-only event this renders through authed-fetch → blob instead of a bare
                // `<img src>`, because `api/media.py` stops serving bytes unauthenticated there.
                <MediaImg
                  eventId={eventId}
                  mediaId={media.mediaId}
                  variant="thumb"
                  alt={media.curator?.caption ?? ""}
                  className="w-full h-auto block object-cover group-hover:scale-105 transition-transform duration-300"
                  fallback={<div className="w-full aspect-square skeleton-shimmer" />}
                />
              ) : (
                <div className="w-full aspect-square skeleton-shimmer" />
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity flex items-end p-2">
                <span className="text-[11px] text-[var(--ivory)] font-medium truncate flex items-center gap-1">
                  <Eye className="w-3 h-3" />
                  <span>View photo</span>
                </span>
              </div>
            </button>
          ))}
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
                className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-full glass-pill text-[var(--accent)] hover:border-[var(--accent)] font-medium"
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
          factors={whyFactorsForGallery(selected, tierByPersonId, rankOf(selected.mediaId))}
          onClose={() => setShowWhy(false)}
        />
      )}
    </section>
  );
}
