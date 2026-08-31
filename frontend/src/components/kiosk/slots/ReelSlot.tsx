"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Volume2 } from "lucide-react";
import type { ReelDoc, ReelSlot as ReelSlotType } from "@/lib/types";
import { cachedReelDoc, listenReel } from "@/lib/firestore";
import { useAccessMode } from "@/lib/eventAccess";
import { useAuthedBlobUrl } from "@/lib/useAuthedImage";
import { isAudioUnlocked, markAudioUnlocked } from "@/lib/audioUnlock";

/** `reel` premiere takeover storyboard (spec 12 §6): title card → play → end card.
 *
 * The pre-roll card is honest about which of three states the reel is actually in: rendering (with the
 * real percentage the job writes at its own stage boundaries — spec 06 §3 step 5, never a fake timer),
 * nothing yet, or a reel that was taken down because a constituent photograph lost eligibility
 * (spec 06 §7). A spinner for all three would be the no-spinner rule broken three ways. */
function preroll(reel: ReelDoc | null): string {
  if (!reel) return "Tonight’s premieres are still in the edit room.";
  switch (reel.status) {
    case "directing":
    case "composing":
      return "Choosing the shots and writing the score…";
    case "rendering":
      return `Rendering · ${reel.progress ?? 0}%`;
    case "unpublished":
      return "Pulled: someone in this film asked not to be shown.";
    case "failed":
      return "Tonight’s premieres are still in the edit room.";
    default:
      return "Tonight’s premieres are still in the edit room.";
  }
}

export function ReelSlot({ eventId, slot }: { eventId: string; slot: ReelSlotType }) {
  // Seeded from the warm cache `lib/kioskPrefetch.ts` filled a slide early. Without this the premiere
  // opened on the title card while a Firestore snapshot travelled, and only *then* started fetching
  // video — two serial round trips in front of the room.
  const [reel, setReel] = useState<ReelDoc | null>(() => cachedReelDoc(eventId, slot.reelId));
  const [ended, setEnded] = useState(false);
  /** Playback has actually begun. The title card stays up until it has, which is the whole point: the
   * card is the deliberate pre-roll, so buffering happens behind something designed rather than behind
   * a black rectangle or a spinner. */
  const [playing, setPlaying] = useState(false);
  /** The browser refused audible playback despite the Start-show gesture. Not silently swallowed — the
   * wall offers one tap to fix it, because a silent film is a degradation worth naming. */
  const [soundBlocked, setSoundBlocked] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setEnded(false);
    setPlaying(false);
    setSoundBlocked(false);
    return listenReel(eventId, slot.reelId, setReel, () => setReel(null));
  }, [eventId, slot.reelId]);

  // `videoUri` points at `api/reels.py`'s 302, which is unauthenticated for a published reel on an
  // **open** event — a `<video>` element cannot send an Authorization header and a kiosk is a
  // television in a venue (spec 09 §3's reasoning, and that path is unchanged). On an invite-only
  // event that branch requires event membership, so the signed URL is resolved first (no blob: a recap
  // film is tens of megabytes and buffering it whole before the first frame is what `<video>` exists
  // to avoid — see `useAuthedBlobUrl`).
  const needsAuth = useAccessMode(eventId) !== "open";
  const authedVideo = useAuthedBlobUrl(needsAuth ? reel?.videoUri ?? null : null);
  const videoSrc = needsAuth ? authedVideo : reel?.videoUri ?? null;

  /** Start playback, once the element says it can run to the end without stalling.
   *
   * The old code did the opposite of this and it is why the wall was silent. It set `autoPlay muted`
   * and then flipped `.muted = false` in `onPlaying`, on the theory that unmuting an already-playing
   * element is not a new autoplay request. It is: Chrome's autoplay policy responds to an unmute
   * without a transient activation by **pausing** the element. So the premiere played muted at best.
   *
   * What works instead is to start unmuted when a gesture has already been spent on audio
   * (`lib/audioUnlock.ts`, called from the Start-show tap) and to treat a rejected `play()` as the
   * signal to fall back — never to start muted and hope to escape it later. */
  const attemptPlay = useCallback(async () => {
    const video = videoRef.current;
    if (!video || ended) return;
    video.volume = 1;
    video.muted = !isAudioUnlocked();
    try {
      await video.play();
      if (!video.muted) markAudioUnlocked();
    } catch {
      // Refused. Retry muted so the room sees the film at all, and offer the one tap that fixes sound.
      video.muted = true;
      setSoundBlocked(true);
      try {
        await video.play();
      } catch {
        // Even muted playback was refused; the title card stays up rather than a frozen frame.
      }
    }
  }, [ended]);

  // The one-tap recovery. A wall has no cursor, so it listens on the whole window rather than drawing a
  // button somebody would have to find — and unmuting *here* is legitimate precisely because it happens
  // inside a real gesture, which is the distinction the failed original missed.
  useEffect(() => {
    if (!soundBlocked) return;
    const enable = () => {
      const video = videoRef.current;
      if (!video) return;
      video.muted = false;
      video.volume = 1;
      void video.play().catch(() => {});
      markAudioUnlocked();
      setSoundBlocked(false);
    };
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });
    return () => {
      window.removeEventListener("pointerdown", enable);
      window.removeEventListener("keydown", enable);
    };
  }, [soundBlocked]);

  if (!videoSrc || ended) {
    return (
      <div
        className="absolute inset-0 flex flex-col items-center justify-center text-center px-[10%]"
        style={{ background: "var(--bg-0)" }}
      >
        {slot.premiere && (
          <p className="font-mono text-xs tracking-[0.25em] mb-4" style={{ color: "var(--accent)" }}>
            TONIGHT&rsquo;S PREMIERE
          </p>
        )}
        <p
          className="font-[family-name:var(--font-display)] mb-4"
          style={{ color: "var(--ivory)", fontSize: "min(9vw, 61px)" }}
        >
          {reel?.title ?? "The Premiere"}
        </p>
        {!reel?.videoUri && <p className="text-sm" style={{ color: "var(--ink-muted)" }}>{preroll(reel)}</p>}
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col" style={{ background: "var(--bg-0)" }}>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <video
        ref={videoRef}
        src={videoSrc}
        // No `autoPlay`: the element is told to buffer, and `onCanPlayThrough` decides when to start.
        // That is what keeps the transition into a premiere free of a stall — the film begins already
        // able to run to the end, behind the title card, rather than starting and then rebuffering.
        preload="auto"
        playsInline
        className={`w-full h-full object-contain transition-opacity duration-500 ${
          playing ? "opacity-100" : "opacity-0"
        }`}
        onCanPlayThrough={() => void attemptPlay()}
        onPlaying={() => setPlaying(true)}
        onEnded={() => setEnded(true)}
        // A stall mid-film is not the same failure as a cold start and must not re-enter the title
        // card; the element recovers on its own and this only re-asserts sound if it was lost.
        onError={() => setEnded(true)}
      />

      {/* The pre-roll title card, held until the first frame is genuinely playing. Same card as the
          no-video branch above, so a premiere never flashes between two different layouts. */}
      {!playing && (
        <div
          className="absolute inset-0 flex flex-col items-center justify-center text-center px-[10%]"
          style={{ background: "var(--bg-0)" }}
        >
          {slot.premiere && (
            <p className="font-mono text-xs tracking-[0.25em] mb-4" style={{ color: "var(--accent)" }}>
              TONIGHT&rsquo;S PREMIERE
            </p>
          )}
          <p
            className="font-[family-name:var(--font-display)]"
            style={{ color: "var(--ivory)", fontSize: "min(9vw, 61px)" }}
          >
            {reel?.title ?? "The Premiere"}
          </p>
        </div>
      )}

      {soundBlocked && playing && (
        <div className="absolute top-[8%] left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 text-xs px-5 py-2.5 rounded-full glass-card border border-white/20 shadow-2xl backdrop-blur-xl bg-slate-950/90 font-mono text-[var(--ivory)]">
          <Volume2 className="w-4 h-4 text-[var(--accent)]" />
          <span>Tap anywhere for sound</span>
        </div>
      )}

      {/* The credit only claims a Lyria score when there is one. `musicUri` is null exactly when
          `music.py` fell back to its silent metronome (a Lyria outage), and printing "soundtrack
          composed by Lyria" over a silent film would be the wall asserting something untrue about
          itself — the one thing a wall must never do. */}
      {playing && (
        <div
          className="absolute inset-x-0 bottom-[6%] text-center font-[family-name:var(--font-display)] italic"
          style={{ color: "var(--ivory)" }}
        >
          {reel?.musicUri
            ? "Directed by Showrunner · soundtrack composed by Lyria"
            : "Directed by Showrunner"}
        </div>
      )}
    </div>
  );
}
