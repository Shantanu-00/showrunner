"use client";

import { useEffect, useMemo, useState } from "react";
import type { MediaDoc } from "@/lib/types";
import { listenHighlights, listenPeopleTiers, listenPublicGallery } from "@/lib/firestore";
import { rankHighlights, whyFactorsForGallery } from "@/lib/scoring";
import { StageChips } from "./StageChips";
import { Lightbox } from "./Lightbox";
import { WhyThisPhoto } from "./WhyThisPhoto";

export function PublicGallery({
  eventId,
  stages,
  judgeMode,
}: {
  eventId: string;
  stages: Array<{ stageId: string; label: string }>;
  judgeMode: boolean;
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
        <p className="text-center text-xs px-4 pb-2" style={{ color: "var(--warn)" }}>
          📶 reconnecting — the gallery will catch up
        </p>
      )}

      <div className="flex items-center gap-2 px-4 pb-3">
        <button
          type="button"
          onClick={() => setMode("recent")}
          className="text-sm px-3 py-1.5 rounded-[var(--radius-pill)]"
          style={{
            background: mode === "recent" ? "var(--accent)" : "transparent",
            color: mode === "recent" ? "var(--bg-0)" : "var(--ink-muted)",
            border: mode === "recent" ? "none" : "var(--hairline)",
          }}
        >
          Recent
        </button>
        <button
          type="button"
          onClick={() => setMode("highlights")}
          className="text-sm px-3 py-1.5 rounded-[var(--radius-pill)]"
          style={{
            background: mode === "highlights" ? "var(--accent)" : "transparent",
            color: mode === "highlights" ? "var(--bg-0)" : "var(--ink-muted)",
            border: mode === "highlights" ? "none" : "var(--hairline)",
          }}
        >
          ✨ Highlights
        </button>
      </div>

      <StageChips stages={stages} active={stageFilter} onChange={setStageFilter} />

      {visible.length === 0 ? (
        <p className="text-center mt-16 px-5" style={{ color: "var(--ink-muted)" }}>
          The kiosk is waiting for its first photo. Scan, shoot, make history.
        </p>
      ) : (
        <div className="columns-2 sm:columns-3 gap-2 px-3 mt-2 [column-fill:_balance]">
          {visible.map((media) => (
            <button
              key={media.mediaId}
              type="button"
              onClick={() => setSelected(media)}
              className="block w-full mb-2 break-inside-avoid rounded-[var(--radius-card)] overflow-hidden"
              style={{ border: "var(--hairline)" }}
            >
              {media.thumbUri ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={media.thumbUri} alt={media.curator?.caption ?? ""} className="w-full h-auto block" />
              ) : (
                <div className="w-full aspect-square skeleton-shimmer" />
              )}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <Lightbox
          media={selected}
          onClose={() => {
            setSelected(null);
            setShowWhy(false);
          }}
          actions={
            judgeMode ? (
              <button
                type="button"
                onClick={() => setShowWhy(true)}
                className="text-sm px-4 py-2 rounded-[var(--radius-pill)]"
                style={{ border: "var(--hairline)", color: "var(--accent)" }}
              >
                Why this photo?
              </button>
            ) : undefined
          }
        />
      )}

      {selected && showWhy && judgeMode && (
        <WhyThisPhoto
          factors={whyFactorsForGallery(selected, tierByPersonId, rankOf(selected.mediaId))}
          onClose={() => setShowWhy(false)}
        />
      )}
    </section>
  );
}
