"use client";

import { useEffect, useState } from "react";
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
      // user cancelled or share-with-files unsupported — fall through to opening the image
    }
  }
  window.open(URL.createObjectURL(blob), "_blank");
}

/** My Album (spec 04 §3, spec 12 §5.2): face-matched grid, lightbox actions = share/save +
 * subject veto (C4) — every photo here already contains the viewer's own face by construction
 * (that is what `albumOf` membership means), so the veto action is always eligible. */
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
    // Optimistic and idempotent: a second tap un-loves. The Story Director never reads this — it
    // only shapes this person's own album ordering and, every 15 reactions, a taste memo (spec 07).
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
      <p className="text-center mt-16 px-5" style={{ color: "var(--ink-muted)" }}>
        Take a selfie and every photo of you finds its way here.
      </p>
    );
  }

  return (
    <>
      <div className="grid grid-cols-3 gap-1.5 px-3 mt-2">
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
            <>
              <button
                type="button"
                onClick={() => void shareOrOpen(eventId, selected)}
                className="text-sm px-4 py-2 rounded-[var(--radius-pill)]"
                style={{ border: "var(--hairline)", color: "var(--ivory)" }}
              >
                📤 Share / save
              </button>
              <button
                type="button"
                disabled={vetoing}
                onClick={() => void onVeto(selected, !selected.subjectVetoes.includes(personId))}
                className="text-sm px-4 py-2 rounded-[var(--radius-pill)] disabled:opacity-50"
                style={{ border: "var(--hairline)", color: "var(--danger)" }}
              >
                {selected.subjectVetoes.includes(personId) ? "🙈 Unhide from public" : "🙈 Hide me from public"}
              </button>
              {vetoError && (
                <p className="text-xs w-full text-center" style={{ color: "var(--danger)" }}>
                  {vetoError}
                </p>
              )}
            </>
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
    <div className="relative aspect-square rounded-[var(--radius-card)] overflow-hidden" style={{ border: "var(--hairline)" }}>
      <button type="button" onClick={() => onSelect(media)} className="absolute inset-0">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" className="w-full h-full object-cover" />
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
        aria-label={loved ? "Un-love this photo" : "Love this photo"}
        className="absolute bottom-1 right-1 text-lg leading-none rounded-full w-7 h-7 flex items-center justify-center"
        style={{ background: "rgba(10,10,10,0.55)" }}
      >
        {loved ? "❤️" : "🤍"}
      </button>
    </div>
  );
}
