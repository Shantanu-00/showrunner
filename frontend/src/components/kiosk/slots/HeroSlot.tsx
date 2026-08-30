"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Camera } from "lucide-react";
import type { HeroSlot as HeroSlotType, MediaDoc } from "@/lib/types";
import { listenMedia, listenUploaderCredit } from "@/lib/firestore";
import { MediaImg } from "@/lib/MediaImg";

function primaryFaceOrigin(media: MediaDoc | null): string {
  if (!media || media.faces.length === 0) return "50% 50%";
  const primary = media.faces.reduce((biggest, f) =>
    f.box.w * f.box.h > biggest.box.w * biggest.box.h ? f : biggest
  );
  const cx = (primary.box.x + primary.box.w / 2) * 100;
  const cy = (primary.box.y + primary.box.h / 2) * 100;
  return `${cx.toFixed(1)}% ${cy.toFixed(1)}%`;
}

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

  // Rendered through `MediaImg` rather than a bare `<img src>`: on an invite-only event the venue TV
  // holds a `members` claim from a kiosk link and fetches the bytes with it, because `api/media.py`
  // no longer serves them unauthenticated there. An open event's wall is unchanged.
  const variant = media?.displayUri ? "display" : media?.thumbUri ? "thumb" : null;

  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "var(--bg-0)" }}>
      {variant ? (
        <>
          {/* B4: `object-cover` cropped a portrait phone photo top/bottom and, worse, kept zooming a
           * wide text-heavy image (an architecture diagram, uploaded during testing) past legibility.
           * `object-contain` never crops, so the blurred cover copy behind it fills the letterboxing
           * instead of leaving hard bars. */}
          <MediaImg
            eventId={eventId}
            mediaId={slot.mediaId}
            variant={variant}
            imgKey={`${slot.mediaId}-backdrop`}
            className="absolute inset-0 w-full h-full object-cover scale-110"
            style={{ filter: "blur(40px) brightness(0.45)" }}
          />
          <MediaImg
            eventId={eventId}
            mediaId={slot.mediaId}
            variant={variant}
            imgKey={slot.mediaId}
            alt={media?.curator?.caption ?? ""}
            className="absolute inset-0 w-full h-full object-contain ken-burns"
            style={
              {
                transformOrigin: primaryFaceOrigin(media),
                "--ken-burns-duration": `${slot.holdSec}s`,
              } as CSSProperties
            }
            fallback={<div className="w-full h-full skeleton-shimmer" />}
          />
        </>
      ) : (
        <div className="w-full h-full skeleton-shimmer" />
      )}

      {/* B4: was a fixed `pt-32 pb-32` (256px) — ~39% of a ~650px laptop content area, and the bottom
       * half collided with the monogram/QR and status glyph pinned at `bottom-[3%]` (Overlays.tsx).
       * `clamp()` keeps the panel proportionate on a 5m TV while guaranteeing enough floor clearance
       * on a laptop for those two fixed-size overlays to never overlap the caption. */}
      <div
        className="absolute inset-x-0 bottom-0 px-[3%]"
        style={{
          paddingTop: "clamp(1rem, 5vh, 5rem)",
          paddingBottom: "clamp(13rem, 22vh, 18rem)",
          background: "linear-gradient(to top, rgba(11, 7, 9, 0.92) 0%, rgba(11, 7, 9, 0.5) 60%, transparent 100%)",
        }}
      >
        {media?.curator?.caption && (
          <p
            className="font-[family-name:var(--font-display)] italic text-3xl sm:text-4xl mb-3 max-w-4xl leading-tight text-[var(--ivory)]"
          >
            &ldquo;{media.curator.caption}&rdquo;
          </p>
        )}
        <span
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full glass-card border border-white/15 text-xs text-[var(--gold-300)] font-medium shadow-lg"
        >
          <Camera className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span>Captured by {creditName ?? "a guest"}</span>
        </span>
      </div>
    </div>
  );
}
