"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Camera } from "lucide-react";
import type { HeroSlot as HeroSlotType, MediaDoc } from "@/lib/types";
import { cachedMediaDoc, listenMedia, listenUploaderCredit } from "@/lib/firestore";
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
  // Seeded synchronously from the document `lib/kioskPrefetch.ts` warmed a slide early. A photograph
  // needs two independent things before it can paint — its bytes *and* the document that says which
  // variant exists and where the faces are — and warming only the first still opened on a shimmer
  // while `listenMedia`'s first snapshot travelled. The listener below then takes over.
  const [media, setMedia] = useState<MediaDoc | null>(() => cachedMediaDoc(eventId, slot.mediaId));
  const [creditName, setCreditName] = useState<string | null>(null);
  // Gates the credit chip on the photograph having actually painted, not on the (much faster)
  // Firestore snapshot — a chip floating over a shimmer reads as a bug. The backstop timer covers a
  // slow or broken image: show the credit anyway rather than hold it hostage to a photo that never
  // loads. Since `lib/kioskPrefetch.ts` warms the next few slides, the common case is now that the
  // image is already decoded and `onLoad` fires on the first frame.
  const [imgReady, setImgReady] = useState(false);

  useEffect(() => {
    return listenMedia(eventId, slot.mediaId, setMedia, () => setMedia(null));
  }, [eventId, slot.mediaId]);

  useEffect(() => {
    if (!media?.uploaderUid) return;
    return listenUploaderCredit(eventId, media.uploaderUid, setCreditName);
  }, [eventId, media?.uploaderUid]);

  useEffect(() => {
    const t = setTimeout(() => setImgReady(true), 1500);
    return () => clearTimeout(t);
  }, [slot.mediaId]);

  const variant = media?.displayUri ? "display" : media?.thumbUri ? "thumb" : null;
  const showCredit = imgReady && Boolean(variant);

  return (
    <div className="absolute inset-0 overflow-hidden select-none" style={{ background: "var(--bg-0)" }}>
      {variant ? (
        <>
          {/* Dynamic Ambient Colored Glow around the Media Container (Theater Frame) */}
          <MediaImg
            eventId={eventId}
            mediaId={slot.mediaId}
            variant={variant}
            imgKey={`${slot.mediaId}-ambient-glow`}
            className="absolute inset-0 w-full h-full object-cover scale-125 theater-ambient-glow"
            style={{ filter: "blur(60px) brightness(0.6) saturate(1.5)" }}
          />

          {/* Secondary Soft Depth Layer */}
          <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" />

          {/* Crisp Centered Media with Ken Burns Motion */}
          <MediaImg
            eventId={eventId}
            mediaId={slot.mediaId}
            variant={variant}
            imgKey={slot.mediaId}
            alt={media?.curator?.caption ?? ""}
            className="absolute inset-0 w-full h-full object-contain ken-burns drop-shadow-[0_0_40px_rgba(0,0,0,0.8)]"
            style={
              {
                transformOrigin: primaryFaceOrigin(media),
                "--ken-burns-duration": `${slot.holdSec}s`,
              } as CSSProperties
            }
            fallback={<div className="w-full h-full skeleton-shimmer" />}
            onLoad={() => setImgReady(true)}
          />
        </>
      ) : (
        <div className="w-full h-full skeleton-shimmer" />
      )}

      {/* Bottom gradient + the uploader credit, and nothing else.
       *
       * Two things used to live here and both are gone deliberately.
       *
       * **The Curator's caption.** A wall is a photograph on a five-metre screen; a machine-written
       * sentence in quote marks over it competes with the picture and dates instantly ("A tender
       * moment as the couple share a glance"). It was also the most visible symptom of the prefetch
       * defect — the caption arrives on a Firestore snapshot in ~200 ms while the photograph took
       * seconds, so the wall showed text alone before its own image. The caption is not deleted, only
       * un-displayed: it still rides the media document, still feeds the reel director's evidence and
       * still explains a ranking in the gallery's "Why this photo?" card, which is where a sentence of
       * prose belongs — on a phone, on request.
       *
       * **The aesthetic score chip.** That number is an internal ranking term. Printing "Score 75"
       * beside somebody's photograph of their friend, on a wall in a room those people are standing
       * in, publishes a judgement of it to everyone there — and invites the obvious question about
       * whoever scored 41.
       *
       * The credit chip stays: it is spec 12 §6's uploader attribution and the social mechanic that
       * makes the next person upload. */}
      <div
        className="absolute inset-x-0 bottom-0 px-[4%]"
        style={{
          paddingTop: "clamp(1.5rem, 5vh, 4rem)",
          paddingBottom: "clamp(10rem, 17vh, 16rem)",
          background:
            "linear-gradient(to top, rgba(8, 10, 18, 0.88) 0%, rgba(8, 10, 18, 0.45) 60%, transparent 100%)",
        }}
      >
        {showCredit && (
          <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full glass-card border border-white/15 text-xs text-[var(--text-primary)] font-medium shadow-2xl backdrop-blur-2xl bg-slate-950/70">
            <Camera className="w-3.5 h-3.5 text-[var(--accent)]" />
            <span>Captured by {creditName ?? "a guest"}</span>
          </span>
        )}
      </div>
    </div>
  );
}
