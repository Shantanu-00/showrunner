"use client";

import { useEffect, useState } from "react";
import { Share2, EyeOff, Eye, Heart, Sparkles, Download, Check } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { listenPrivateAlbum, listenReactions, setReaction, type Reaction } from "@/lib/firestore";
import { ApiError, mediaRenderPath, setSubjectVeto } from "@/lib/api";
import { mediaBlob, saveMediaToDisk, resolveSignedUrl } from "@/lib/mediaUrls";
import { useAuthedImage } from "@/lib/useAuthedImage";
import { Lightbox } from "@/components/gallery/Lightbox";
import { GlowButton } from "@/components/atoms/GlowButton";
import { useHaptics } from "@/lib/useHaptics";

async function shareOrOpen(eventId: string, media: MediaDoc) {
  const variant = media.displayUri ? "display" : media.thumbUri ? "thumb" : null;
  if (!variant) return;
  const path = mediaRenderPath(eventId, media.mediaId, variant);
  const blob = await mediaBlob(path);
  if (!blob) {
    const url = await resolveSignedUrl(path);
    if (url) window.open(url, "_blank", "noopener");
    return;
  }
  if (navigator.share) {
    try {
      const file = new File([blob], `${media.mediaId}.jpg`, { type: blob.type || "image/webp" });
      await navigator.share({ files: [file] });
      return;
    } catch {
      // user cancelled
    }
  }
  window.open(URL.createObjectURL(blob), "_blank");
}

export function AlbumGrid({ eventId, personId }: { eventId: string; personId: string }) {
  const [items, setItems] = useState<MediaDoc[]>([]);
  const [selected, setSelected] = useState<MediaDoc | null>(null);
  const [vetoing, setVetoing] = useState(false);
  const [vetoError, setVetoError] = useState<string | null>(null);
  const [reactions, setReactions] = useState<Record<string, Reaction>>({});
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadedSuccess, setDownloadedSuccess] = useState(false);
  const { tapHaptic, successHaptic } = useHaptics();

  useEffect(() => {
    return listenPrivateAlbum(eventId, personId, setItems, () => setItems([]));
  }, [eventId, personId]);

  useEffect(() => {
    return listenReactions(eventId, personId, setReactions);
  }, [eventId, personId]);

  function onLove(mediaId: string) {
    tapHaptic();
    const next: Reaction | null = reactions[mediaId] === "love" ? null : "love";
    setReactions((prev) => {
      const copy = { ...prev };
      if (next) copy[mediaId] = next;
      else delete copy[mediaId];
      return copy;
    });
    void setReaction(eventId, personId, mediaId, next);
  }

  async function onVeto(media: MediaDoc, hide: boolean) {
    tapHaptic();
    setVetoing(true);
    setVetoError(null);
    try {
      await setSubjectVeto(eventId, media.mediaId, hide);
      setSelected(null);
      successHaptic();
    } catch (err) {
      setVetoError(
        err instanceof ApiError
          ? "Couldn't reach the director yet — try again in a moment."
          : "Something went wrong — try again."
      );
    } finally {
      setVetoing(false);
    }
  }

  // 1-Tap Download All trigger
  async function handleDownloadAll() {
    if (items.length === 0 || downloadingAll) return;
    tapHaptic();
    setDownloadingAll(true);
    try {
      for (const media of items) {
        const variant = media.displayUri ? "display" : media.thumbUri ? "thumb" : null;
        if (!variant) continue;
        await saveMediaToDisk(
          mediaRenderPath(eventId, media.mediaId, variant),
          `showrunner-${media.mediaId}.jpg`
        );
        // Stagger browser downloads slightly
        await new Promise((r) => setTimeout(r, 200));
      }
      successHaptic();
      setDownloadedSuccess(true);
      setTimeout(() => setDownloadedSuccess(false), 3000);
    } catch {
      // safe fallback
    } finally {
      setDownloadingAll(false);
    }
  }

  if (items.length === 0) {
    return (
      <div className="text-center mt-12 px-6 py-14 rounded-3xl glass-card mx-4 border border-dashed border-white/10 shadow-2xl animate-spring-in">
        <div className="w-16 h-16 rounded-full bg-[var(--accent)]/15 border border-[var(--accent)]/30 flex items-center justify-center mx-auto mb-4 text-[var(--accent)] shadow-lg">
          <Sparkles className="w-8 h-8 animate-pulse stroke-[1.8]" />
        </div>
        <h3 className="font-[family-name:var(--font-display)] text-xl font-semibold text-[var(--text-primary)] mb-2">
          No moments found yet
        </h3>
        <p className="text-xs text-[var(--text-secondary)] max-w-xs mx-auto leading-relaxed">
          New uploads arrive live. As guests and event photographers shoot, any photo featuring your face will automatically appear here in real time.
        </p>
      </div>
    );
  }

  return (
    <div className="pb-24">
      {/* Header bar with photo count */}
      <div className="flex items-center justify-between px-4 pt-1 pb-2">
        <span className="text-xs font-mono tabular-nums text-[var(--text-secondary)]">
          {items.length} matched {items.length === 1 ? "photo" : "photos"}
        </span>
        <span className="text-[11px] font-mono uppercase tracking-wider text-[var(--emerald-live)] flex items-center gap-1.5 font-semibold">
          <span className="live-dot" />
          <span>Syncing Live</span>
        </span>
      </div>

      {/* Matched Photos Grid with Staggered Spring Entrance */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 px-3">
        {items.map((media, index) => (
          <AlbumThumb
            key={media.mediaId}
            eventId={eventId}
            media={media}
            index={index}
            loved={reactions[media.mediaId] === "love"}
            onSelect={setSelected}
            onLove={onLove}
          />
        ))}
      </div>

      {/* Sticky Bottom Floating Action Bar for 1-Tap Download */}
      <div className="fixed inset-x-0 bottom-20 z-30 px-4 flex justify-center pointer-events-none">
        <div className="pointer-events-auto max-w-md w-full animate-spring-in">
          <GlowButton
            variant="primary"
            size="md"
            fullWidth
            loading={downloadingAll}
            icon={downloadedSuccess ? Check : Download}
            onClick={() => void handleDownloadAll()}
            className="shadow-2xl font-mono tabular-nums text-xs sm:text-sm tracking-wide"
          >
            {downloadedSuccess
              ? "All Photos Saved!"
              : downloadingAll
              ? "Saving Match Album…"
              : `Download All (${items.length}) Photos`}
          </GlowButton>
        </div>
      </div>

      {selected && (
        <Lightbox
          eventId={eventId}
          media={selected}
          onClose={() => setSelected(null)}
          actions={
            <div className="flex flex-wrap gap-2.5 justify-center">
              <button
                type="button"
                onClick={() => void shareOrOpen(eventId, selected)}
                className="flex items-center gap-2 text-xs px-4 py-2.5 rounded-full glass-card hover:border-[var(--accent)] text-[var(--text-primary)] font-medium active:scale-95 transition-transform cursor-pointer"
              >
                <Share2 className="w-4 h-4 text-[var(--accent)]" />
                <span>Share / Save</span>
              </button>
              <button
                type="button"
                disabled={vetoing}
                onClick={() => void onVeto(selected, !selected.subjectVetoes.includes(personId))}
                className="flex items-center gap-2 text-xs px-4 py-2.5 rounded-full glass-card border border-red-500/30 hover:border-red-500 text-red-400 font-medium active:scale-95 transition-transform disabled:opacity-50 cursor-pointer"
              >
                {selected.subjectVetoes.includes(personId) ? (
                  <>
                    <Eye className="w-4 h-4" />
                    <span>Unhide from Public</span>
                  </>
                ) : (
                  <>
                    <EyeOff className="w-4 h-4" />
                    <span>Hide Me from Public Wall</span>
                  </>
                )}
              </button>
              {vetoError && (
                <p className="text-xs w-full text-center text-red-400 mt-1">
                  {vetoError}
                </p>
              )}
            </div>
          }
        />
      )}
    </div>
  );
}

function AlbumThumb({
  eventId,
  media,
  index,
  loved,
  onSelect,
  onLove,
}: {
  eventId: string;
  media: MediaDoc;
  index: number;
  loved: boolean;
  onSelect: (media: MediaDoc) => void;
  onLove: (mediaId: string) => void;
}) {
  const src = useAuthedImage(eventId, media.thumbUri ? media.mediaId : null, "thumb");

  return (
    <div
      className="relative aspect-square rounded-2xl overflow-hidden glass-card shadow-lg group border border-white/10 hover:border-[var(--accent)]/50 transition-all duration-300 transform active:scale-[0.98] animate-spring-in"
      style={{ animationDelay: `${Math.min(index * 50, 400)}ms` }}
    >
      <button
        type="button"
        onClick={() => onSelect(media)}
        className="absolute inset-0 cursor-pointer"
        aria-label="View matched photo"
      >
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt=""
            loading="lazy"
            decoding="async"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full skeleton-shimmer" />
        )}
      </button>

      {/* Favorite / Heart Button */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onLove(media.mediaId);
        }}
        aria-label={loved ? "Un-favorite this photo" : "Favorite this photo"}
        className={`absolute bottom-2 right-2 p-2 rounded-full transition-all backdrop-blur-md cursor-pointer ${
          loved
            ? "bg-rose-500 text-white shadow-lg scale-110"
            : "bg-slate-950/70 text-white/70 hover:text-white hover:bg-slate-950/90 border border-white/10"
        }`}
      >
        <Heart className={`w-3.5 h-3.5 ${loved ? "fill-current" : ""}`} />
      </button>
    </div>
  );
}
