"use client";

import { useEffect, useRef, useState } from "react";
import type { ReelDoc, ReelSlot as ReelSlotType } from "@/lib/types";
import { listenReel } from "@/lib/firestore";
import { useAccessMode } from "@/lib/eventAccess";
import { useAuthedBlobUrl } from "@/lib/useAuthedImage";

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
  const [reel, setReel] = useState<ReelDoc | null>(null);
  const [ended, setEnded] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setEnded(false);
    return listenReel(eventId, slot.reelId, setReel, () => setReel(null));
  }, [eventId, slot.reelId]);

  // `videoUri` points at `api/reels.py`'s 302, which is unauthenticated for a published reel on an
  // **open** event — a `<video>` element cannot send an Authorization header and a kiosk is a
  // television in a venue (spec 09 §3's reasoning, and that path is unchanged). On an invite-only
  // event that branch now requires event membership, so the file is fetched with the TV's token and
  // played from a blob instead. A reel is a few megabytes; a photograph-sized concession.
  const needsAuth = useAccessMode(eventId) !== "open";
  const authedVideo = useAuthedBlobUrl(needsAuth ? reel?.videoUri ?? null : null);
  const videoSrc = needsAuth ? authedVideo : reel?.videoUri ?? null;

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
          {reel?.title ?? "The Couple"}
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
        autoPlay
        muted
        playsInline
        className="w-full h-full object-contain"
        onEnded={() => setEnded(true)}
        // Browsers only allow autoplay-with-sound after a user gesture, and a kiosk's "Start show"
        // tap happened minutes or hours before this element even mounts. Starting muted (which every
        // browser allows) and then unmuting once playback has actually begun sidesteps that gate
        // entirely — toggling `.muted` on an already-playing element isn't a new autoplay request.
        onPlaying={() => {
          if (videoRef.current) videoRef.current.muted = false;
        }}
      />
      <div
        className="absolute inset-x-0 bottom-[6%] text-center font-[family-name:var(--font-display)] italic"
        style={{ color: "var(--ivory)" }}
      >
        Directed by Showrunner · soundtrack composed by Lyria
      </div>
    </div>
  );
}
