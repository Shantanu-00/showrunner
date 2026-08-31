"use client";

import { useEffect, useState } from "react";
import { Film, Download } from "lucide-react";
import { listenRecapReel } from "@/lib/firestore";
import { useAuthedBlobUrl } from "@/lib/useAuthedImage";
import type { ReelDoc } from "@/lib/types";

/** The event's recap film, on the phone of somebody who was there.
 *
 * Until now a finished recap existed in exactly two places: the kiosk (a TV most guests on a trip
 * never stand in front of) and the host console's wrap panel. The guests the film is *made of* had no
 * way to see it, which is a strange place for the product to end.
 *
 * **Why the blob and not a plain `<video src>`.** `api/reels.py` now requires event membership for
 * `?download=1` on every event, open ones included — watching is a kiosk in a venue, but *keeping a
 * copy* leaves the consent interlock behind for good, and spec 06 §7 can pull a reel off every
 * surface it still controls the moment somebody in it objects. It cannot reach into a camera roll. So
 * the bytes come through `useAuthedBlobUrl` (authed fetch → follow the 302 → blob), which also means
 * one fetch backs both the player and the save button: a `download` attribute on an anchor pointing at
 * a blob forces the save dialog regardless of the response's own content-disposition. Same trick the
 * host's `WrapReportPanel` uses, for the same reason.
 *
 * Renders nothing at all until a recap is published. A guest mid-trip should not see an empty "your
 * film" placeholder promising something that only exists once the host wraps.
 */
export function RecapCard({ eventId }: { eventId: string }) {
  const [reel, setReel] = useState<(ReelDoc & { version?: number }) | null>(null);

  useEffect(
    () => listenRecapReel(eventId, setReel, () => setReel(null)),
    [eventId]
  );

  const reelId = reel?.reelId ?? null;
  // The listener only ever returns `visibility == 'public'` documents (the rule denies anything else
  // to a member), so reaching this line already means the reel is watchable — no second status check.
  const videoSrc = useAuthedBlobUrl(
    reelId ? `/v1/events/${eventId}/reels/${reelId}/video?download=1` : null
  );

  if (!reel || !reelId) return null;

  return (
    <div className="rounded-2xl glass-card p-4 border border-[var(--accent)]/25 shadow-lg">
      <div className="flex items-center gap-3 mb-3">
        <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
          <Film className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-semibold text-[var(--ivory)] truncate">
            {reel.title || "Your event recap"}
          </h4>
          <p className="text-xs text-[var(--ink-muted)]">
            Cut by the director, scored by Lyria
            {reel.durationSec ? ` · ${Math.round(reel.durationSec)}s` : ""}
          </p>
        </div>
      </div>

      {videoSrc ? (
        <>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <video src={videoSrc} controls playsInline className="w-full rounded-xl bg-black" />
          <a
            href={videoSrc}
            download={`showrunner-recap-${reelId}.mp4`}
            className="btn-secondary mt-3 inline-flex items-center gap-1.5 text-[11px] px-3.5 py-1.5 rounded-full font-medium"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Save to my phone</span>
          </a>
        </>
      ) : (
        <div className="h-40 rounded-xl skeleton-shimmer bg-white/5" />
      )}
    </div>
  );
}
