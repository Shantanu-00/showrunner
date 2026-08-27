"use client";

import { useEffect, useState } from "react";
import type { MediaDoc } from "@/lib/types";
import { listenPrivateAlbum } from "@/lib/firestore";
import { ApiError, setSubjectVeto } from "@/lib/api";
import { Lightbox } from "@/components/gallery/Lightbox";

async function shareOrOpen(media: MediaDoc) {
  const src = media.displayUri ?? media.thumbUri;
  if (!src) return;
  if (navigator.share) {
    try {
      const res = await fetch(src);
      const blob = await res.blob();
      const file = new File([blob], `${media.mediaId}.jpg`, { type: blob.type || "image/jpeg" });
      await navigator.share({ files: [file] });
      return;
    } catch {
      // user cancelled or share-with-files unsupported — fall through to opening the image
    }
  }
  window.open(src, "_blank");
}

/** My Album (spec 04 §3, spec 12 §5.2): face-matched grid, lightbox actions = share/save +
 * subject veto (C4) — every photo here already contains the viewer's own face by construction
 * (that is what `albumOf` membership means), so the veto action is always eligible. */
export function AlbumGrid({ eventId, personId }: { eventId: string; personId: string }) {
  const [items, setItems] = useState<MediaDoc[]>([]);
  const [selected, setSelected] = useState<MediaDoc | null>(null);
  const [vetoing, setVetoing] = useState(false);
  const [vetoError, setVetoError] = useState<string | null>(null);

  useEffect(() => {
    return listenPrivateAlbum(eventId, personId, setItems, () => setItems([]));
  }, [eventId, personId]);

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
          <button
            key={media.mediaId}
            type="button"
            onClick={() => setSelected(media)}
            className="aspect-square rounded-[var(--radius-card)] overflow-hidden"
            style={{ border: "var(--hairline)" }}
          >
            {media.thumbUri ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={media.thumbUri} alt="" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full skeleton-shimmer" />
            )}
          </button>
        ))}
      </div>

      {selected && (
        <Lightbox
          media={selected}
          onClose={() => setSelected(null)}
          actions={
            <>
              <button
                type="button"
                onClick={() => void shareOrOpen(selected)}
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
