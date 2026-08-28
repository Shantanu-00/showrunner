"use client";

import { useEffect, useState, type CSSProperties } from "react";
import type { HeroSlot as HeroSlotType, MediaDoc } from "@/lib/types";
import { listenMedia, listenUploaderCredit } from "@/lib/firestore";
import { mediaRenderUrl } from "@/lib/api";

function primaryFaceOrigin(media: MediaDoc | null): string {
  if (!media || media.faces.length === 0) return "50% 50%";
  const primary = media.faces.reduce((biggest, f) =>
    f.box.w * f.box.h > biggest.box.w * biggest.box.h ? f : biggest
  );
  const cx = (primary.box.x + primary.box.w / 2) * 100;
  const cy = (primary.box.y + primary.box.h / 2) * 100;
  return `${cx.toFixed(1)}% ${cy.toFixed(1)}%`;
}

/** `hero` — face-anchored Ken Burns, Curator's caption, the credit chip (spec 12 §6). Never
 * crops a head: the pan's transform-origin is the primary face box center, so the zoom holds
 * on the face while everything else drifts away from it (§4's "pan away from the face box"). */
export function HeroSlot({ eventId, slot }: { eventId: string; slot: HeroSlotType }) {
  const [media, setMedia] = useState<MediaDoc | null>(null);
  const [creditName, setCreditName] = useState<string | null>(null);

  useEffect(() => {
    return listenMedia(eventId, slot.mediaId, setMedia, () => setMedia(null));
  }, [eventId, slot.mediaId]);

  useEffect(() => {
    if (!media?.uploaderUid) return;
    return listenUploaderCredit(eventId, media.uploaderUid, setCreditName);
  }, [eventId, media?.uploaderUid]);

  const src = media?.displayUri
    ? mediaRenderUrl(eventId, slot.mediaId, "display")
    : media?.thumbUri
      ? mediaRenderUrl(eventId, slot.mediaId, "thumb")
      : null;

  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "var(--bg-0)" }}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={slot.mediaId}
          src={src}
          alt={media?.curator?.caption ?? ""}
          className="w-full h-full object-cover ken-burns"
          style={
            {
              transformOrigin: primaryFaceOrigin(media),
              "--ken-burns-duration": `${slot.holdSec}s`,
            } as CSSProperties
          }
        />
      ) : (
        <div className="w-full h-full skeleton-shimmer" />
      )}

      <div
        className="absolute inset-x-0 bottom-0 pt-24 pb-32 px-[3%]"
        style={{ background: "linear-gradient(to top, rgb(11 7 9 / 0.85), transparent)" }}
      >
        {media?.curator?.caption && (
          <p
            className="font-[var(--font-display)] italic text-3xl mb-3 max-w-3xl"
            style={{ color: "var(--ivory)" }}
          >
            {media.curator.caption}
          </p>
        )}
        <span
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-[var(--radius-pill)] text-sm"
          style={{ background: "var(--bg-glass)", border: "var(--hairline)", color: "var(--gold-300)" }}
        >
          📸 {creditName ?? "a guest"}
        </span>
      </div>
    </div>
  );
}
