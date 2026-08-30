"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Camera, Sparkles } from "lucide-react";
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
  // Gates the caption/credit reveal on the actual photo having painted, not on the (much faster)
  // Firestore snapshot that carries its caption text — otherwise every slide flashes caption-alone
  // for the ~2-3s the image takes to fetch. The fallback timer is a backstop for a slow/broken image:
  // reveal the caption anyway rather than hold the slide hostage to a photo that never loads.
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
  const showCaption = imgReady && Boolean(variant);

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

      {/* Bottom Gradient Panel & Caption Card */}
      <div
        className="absolute inset-x-0 bottom-0 px-[4%]"
        style={{
          paddingTop: "clamp(2rem, 8vh, 6rem)",
          paddingBottom: "clamp(12rem, 20vh, 18rem)",
          background: "linear-gradient(to top, rgba(8, 10, 18, 0.95) 0%, rgba(8, 10, 18, 0.6) 65%, transparent 100%)",
        }}
      >
        {showCaption && (
          <>
            {media?.curator?.caption && (
              <p
                className="font-[family-name:var(--font-display)] italic text-3xl sm:text-4xl md:text-5xl mb-3.5 max-w-4xl leading-tight text-[var(--text-primary)] drop-shadow-lg"
              >
                &ldquo;{media.curator.caption}&rdquo;
              </p>
            )}
            <div className="flex items-center gap-3">
              <span
                className="inline-flex items-center gap-2 px-4 py-2 rounded-full glass-card border border-white/15 text-xs text-[var(--text-primary)] font-medium shadow-2xl backdrop-blur-2xl bg-slate-950/70"
              >
                <Camera className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>Captured by {creditName ?? "a guest"}</span>
              </span>

              {media?.curator?.aestheticScore && (
                <span
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-full glass-card border border-white/10 text-xs font-mono tabular-nums text-[var(--text-secondary)] bg-slate-950/60"
                >
                  <Sparkles className="w-3 h-3 text-[var(--accent)]" />
                  <span>Score {Math.round(media.curator.aestheticScore)}</span>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
