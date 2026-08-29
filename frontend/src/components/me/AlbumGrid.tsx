"use client";

import { useEffect, useState } from "react";
import { Share2, EyeOff, Eye, Heart, Sparkles } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { listenPrivateAlbum, listenReactions, setReaction, type Reaction } from "@/lib/firestore";
import { ApiError, authedFetch, mediaRenderPath, setSubjectVeto } from "@/lib/api";
import { useAuthedImage } from "@/lib/useAuthedImage";
import { Lightbox } from "@/components/gallery/Lightbox";

async function shareOrOpen(eventId: string, media: MediaDoc) {
  const variant = media.displayUri ? "display" : media.thumbUri ? "thumb" : null;
  if (!variant) return;
  const res = await authedFetch(mediaRenderPath(eventId, media.mediaId, variant), { method: "GET" });
  if (!res.ok) return;
  const blob = await res.blob();
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

  useEffect(() => {
    return listenPrivateAlbum(eventId, personId, setItems, () => setItems([]));
  }, [eventId, personId]);

  useEffect(() => {
    return listenReactions(eventId, personId, setReactions);
  }, [eventId, personId]);

  function onLove(mediaId: string) {
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
    setVetoing(true);
    setVetoError(null);
    try {
      await setSubjectVeto(eventId, media.mediaId, hide);
      setSelected(null);
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

  if (items.length === 0) {
    return (
      <div className="text-center mt-12 px-6 py-12 rounded-2xl glass-card mx-4 border border-dashed border-white/10">
        <Sparkles className="w-8 h-8 text-[var(--accent)] mx-auto mb-2 opacity-60" />
        <p className="font-[family-name:var(--font-display)] text-base text-[var(--ivory)] mb-1">
          No matches found yet
        </p>
        <p className="text-xs text-[var(--ink-muted)] max-w-xs mx-auto">
          As guests upload photos, any moment containing your face will automatically stream into this private album.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="grid grid-cols-3 gap-2 px-3 mt-2">
        {items.map((media) => (
          <AlbumThumb
            key={media.mediaId}
            eventId={eventId}
            media={media}
            loved={reactions[media.mediaId] === "love"}
            onSelect={setSelected}
            onLove={onLove}
          />
        ))}
      </div>

      {selected && (
        <Lightbox
          eventId={eventId}
          media={selected}
          onClose={() => setSelected(null)}
          actions={
            <div className="flex flex-wrap gap-2 justify-center">
              <button
                type="button"
                onClick={() => void shareOrOpen(eventId, selected)}
                className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-full glass-card hover:border-[var(--accent)] text-[var(--ivory)] font-medium"
              >
                <Share2 className="w-3.5 h-3.5" />
                <span>Share / Save</span>
              </button>
              <button
                type="button"
                disabled={vetoing}
                onClick={() => void onVeto(selected, !selected.subjectVetoes.includes(personId))}
                className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-full glass-card border border-[var(--danger)]/40 hover:border-[var(--danger)] text-[var(--danger)] font-medium disabled:opacity-50"
              >
                {selected.subjectVetoes.includes(personId) ? (
                  <>
                    <Eye className="w-3.5 h-3.5" />
                    <span>Unhide from Public</span>
                  </>
                ) : (
                  <>
                    <EyeOff className="w-3.5 h-3.5" />
                    <span>Hide Me from Public Wall</span>
                  </>
                )}
              </button>
              {vetoError && (
                <p className="text-xs w-full text-center text-[var(--danger)] mt-1">
                  {vetoError}
                </p>
              )}
            </div>
          }
        />
      )}
    </>
  );
}

function AlbumThumb({
  eventId,
  media,
  loved,
  onSelect,
  onLove,
}: {
  eventId: string;
  media: MediaDoc;
  loved: boolean;
  onSelect: (media: MediaDoc) => void;
  onLove: (mediaId: string) => void;
}) {
  const src = useAuthedImage(eventId, media.thumbUri ? media.mediaId : null, "thumb");
  return (
    <div className="relative aspect-square rounded-2xl overflow-hidden glass-card shadow-md group">
      <button type="button" onClick={() => onSelect(media)} className="absolute inset-0">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200" />
        ) : (
          <div className="w-full h-full skeleton-shimmer" />
        )}
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onLove(media.mediaId);
        }}
        aria-label={loved ? "Un-favorite this photo" : "Favorite this photo"}
        className={`absolute bottom-2 right-2 p-1.5 rounded-full transition-all backdrop-blur-md ${
          loved
            ? "bg-rose-500 text-white shadow-lg scale-110"
            : "bg-black/60 text-white/70 hover:text-white hover:bg-black/80"
        }`}
      >
        <Heart className={`w-3.5 h-3.5 ${loved ? "fill-current" : ""}`} />
      </button>
    </div>
  );
}
