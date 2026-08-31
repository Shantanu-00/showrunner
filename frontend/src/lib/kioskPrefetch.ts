"use client";

// Warm the kiosk's *next* slides while the current one is on screen.
//
// The kiosk was already thinking far enough ahead — `publisher/program.py` writes a whole
// ~5-minute programme into `kiosk/playlist` and the client holds every slot in memory. What it did
// not do was fetch a single byte until the slide was already being rendered, so every transition
// showed a shimmer while the browser walked API → 302 → GCS. That read as "the AI is slow" when the
// AI had made its decisions minutes earlier; the wall was just downloading late.
//
// This closes it with the smallest thing that works: on every slot change, warm the next
// `PREFETCH_DEPTH` slides. Two paths, because the kiosk has two byte paths (`lib/MediaImg.tsx`):
// an **open** event's photos are plain URLs, warmed through the browser's own HTTP cache; an
// **invite-only** event's need a bearer token, so they are warmed into the shared blob store in
// `lib/mediaUrls.ts` that the renderer reads from too. Either way the `<img>` finds bytes already
// present instead of starting a request.
//
// **Coverage is first slide to last, not just the middle.** Three things together:
//   - the first paint is prefetched too, from the moment a playlist arrives (`slotIndex === 0` runs
//     this hook like any other index);
//   - the window **wraps** with the modulo the renderer itself uses, so approaching the end of the
//     programme warms the beginning it is about to loop back to;
//   - a playlist revision re-runs it, so a programme the publisher rewrote mid-show is warm before
//     `KioskShow` resets to slot 0 (a `leadKey` change makes it restart from the top — S8a).
//
// What is deliberately not prefetched: `reel` slots. A reel is an MP4 served by a redirect that
// re-checks `visibility` on every request and mints a 60-minute signed URL; pre-fetching one would
// download tens of megabytes for a slide that may never be reached, and `<video>` already streams
// and buffers on its own. `leaderboard`/`collage`/`bounty_call` carry no photographic bytes at all.

import { useEffect } from "react";
import { mediaRenderPath, mediaRenderUrl } from "./api";
import { cacheBytes, cacheKeyFor, resolveSignedUrl, warmHttpCache } from "./mediaUrls";
import type { KioskSlot } from "./types";

/** How many slides ahead to warm. Three is the useful number rather than an arbitrary one: at a 6 s
 * hero hold it buys ~18 s of runway, which covers a cold GCS fetch on venue wifi several times over,
 * while keeping the working set small enough that a five-day kiosk never accumulates memory. Warming
 * the whole ~5-minute programme would download ~50 photographs the wall may re-decide before showing. */
const PREFETCH_DEPTH = 3;

/** Every media id a slot will need pixels for, in the order it will need them. `just_in` is a
 * filmstrip, so it names several — capped, because a burst can list more than a strip shows. */
function mediaIdsOf(slot: KioskSlot): string[] {
  switch (slot.type) {
    case "hero":
      return slot.mediaId ? [slot.mediaId] : [];
    case "just_in":
      return (slot.mediaIds ?? []).slice(0, 6);
    default:
      return [];
  }
}

/**
 * Prefetch the next few slides' images. Call once from the kiosk shell.
 *
 * `needsAuth` mirrors `MediaImg`'s own choice exactly — it must, or the prefetch warms one path while
 * the renderer reads the other and the whole thing silently does nothing. Pass `undefined` while the
 * event bootstrap is still in flight and this no-ops rather than guessing: one slide's worth of
 * latency is cheaper than warming ~50 wrong URLs.
 */
export function useSlotPrefetch(
  eventId: string,
  slots: KioskSlot[],
  slotIndex: number,
  needsAuth: boolean | undefined,
  revision: number | undefined
): void {
  useEffect(() => {
    if (needsAuth === undefined || slots.length === 0) return;

    const ids: string[] = [];
    for (let step = 1; step <= PREFETCH_DEPTH; step += 1) {
      // The same modulo `KioskShow` uses to pick the active slot, so the window wraps at the end of
      // the programme into the slides the wall is about to loop back around to.
      const slot = slots[(slotIndex + step) % slots.length];
      if (slot) ids.push(...mediaIdsOf(slot));
    }
    // The current slide too, for the case that matters most on a wall: the very first paint after a
    // playlist arrives, where nothing has been warmed yet. A hit is free.
    const current = slots[slotIndex % slots.length];
    if (current) ids.unshift(...mediaIdsOf(current));

    for (const mediaId of Array.from(new Set(ids))) {
      if (needsAuth) {
        // Resolve the signed URL, then pull the bytes into `lib/mediaUrls.ts`'s durable store — the
        // same store `useMediaSrc` reads first, so a warmed slide paints on its first render.
        const key = cacheKeyFor(eventId, mediaId, "display");
        void resolveSignedUrl(mediaRenderPath(eventId, mediaId, "display")).then((url) => {
          if (url) void cacheBytes(key, url);
        });
      } else {
        warmHttpCache(mediaRenderUrl(eventId, mediaId, "display"));
      }
    }
    // `revision` is in the deps because a rewritten programme is a different set of slides even at
    // the same index — and the publisher only writes a revision when its *decisions* changed (S8a),
    // so this cannot spin on the recency-decay rebuilds that leave the programme identical.
  }, [eventId, slots, slotIndex, needsAuth, revision]);
}
