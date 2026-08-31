"use client";

// Warm the next ~10 seconds of the wall, and tell the wall when it may start.
//
// `publisher/program.py` writes a whole ~5-minute programme into `kiosk/playlist` and the client holds
// every slot in memory, so the *decisions* were always minutes ahead. The *bytes* were not: nothing
// was fetched until a slide was already rendering, so every transition showed a shimmer while the
// browser walked API → 302 → GCS. That reads as "the AI is slow" when the AI decided minutes earlier
// and the wall was merely downloading late.
//
// An earlier fix warmed a fixed three slides ahead. Two things were still wrong with it:
//
//  1. **Three slides is not a duration.** At a 6 s hero hold it buys 18 s; at a run of 8 s
//     leaderboards it buys 24 s; behind a reel it buys almost nothing, because a reel's own hold is
//     unknown to the programme. A wall wants a *time* budget, so this module accumulates
//     `slotHoldSec` forward until it has covered `LOOKAHEAD_MS`.
//  2. **Nothing gated on the result.** The show started at slot 0 and advanced on a fixed timer
//     whether the bytes had landed or not, so the very first paint — the one a room is watching —
//     was the most likely shimmer of the whole evening. This hook now reports readiness per slot and
//     `KioskShow` holds the start and each advance on it.
//
// Three byte paths, because the kiosk has three:
//   - an **open** event's photos are plain URLs, warmed through the browser's own HTTP cache;
//   - an **invite-only** event's need a bearer token, so they are warmed into the shared blob store in
//     `lib/mediaUrls.ts` that the renderer reads from too;
//   - a **reel** is an MP4 behind a redirect that re-checks `visibility` and mints a signed URL. The
//     bytes are deliberately still not downloaded here — tens of megabytes for a slide that may never
//     be reached, and `<video preload="auto">` streams and buffers perfectly well on its own — but the
//     *URL resolution* is warmed, which is the round trip that used to happen with a black rectangle
//     on the wall.
//
// And the fourth thing a slide needs, which is not bytes at all: its **media document**. `HeroSlot`
// cannot choose a variant or anchor its Ken Burns origin without one, so a slide with warm pixels and
// a cold document still opened on a shimmer. `prefetchMediaDoc` closes that (`lib/firestore.ts`).
//
// Coverage is first slide to last: slot 0 is warmed the moment a playlist arrives, the window **wraps**
// with the same modulo the renderer uses (so the end of the programme warms the beginning it is about
// to loop into), and a playlist revision re-runs everything.

import { useCallback, useEffect, useRef, useState } from "react";
import { mediaRenderPath, mediaRenderUrl } from "./api";
import { prefetchMediaDoc, prefetchReelDoc } from "./firestore";
import { slotHoldSec } from "./kiosk";
import { cacheBytes, cacheKeyFor, resolveSignedUrl, warmHttpCache } from "./mediaUrls";
import type { KioskSlot } from "./types";

/** How far ahead, in wall-clock show time, to have bytes on hand. The room's complaint is never "that
 * took 300 ms" — it is seeing a loading state at all — so this is sized to cover a cold GCS fetch on
 * venue wifi several times over while keeping the working set small enough that a five-day kiosk never
 * accumulates memory. */
const LOOKAHEAD_MS = 10_000;

/** A hard stop on the window regardless of how short the holds are, so a programme of one-second
 * slides cannot turn a 10-second budget into a download of the whole ~5-minute run. */
const MAX_LOOKAHEAD_SLOTS = 8;

/** A reel owns its own timing (`kiosk.ts::slotHoldSec` returns 0 for it), so it contributes nothing to
 * the accumulator and the window would run straight past it. A premiere is tens of seconds; counting
 * it as more than the whole budget stops the walk there, which is correct — everything after a reel is
 * at least a reel away. */
const REEL_ASSUMED_MS = LOOKAHEAD_MS + 1;

/** How long the wall will wait for a slide before showing it anyway. A photograph whose bytes are
 * genuinely unavailable — a deleted object, a bucket 403 — must never freeze the programme; after this
 * the slot is treated as ready and the renderer's own fallback handles it. */
export const READY_TIMEOUT_MS = 4_000;

/** Every media id a slot needs pixels for, in the order it needs them. `just_in` is a filmstrip, so it
 * names several — capped, because a burst can list more than a strip shows. */
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

function holdMsOf(slot: KioskSlot): number {
  if (slot.type === "reel") return REEL_ASSUMED_MS;
  return (slotHoldSec(slot) || 8) * 1000;
}

/** The slot indices this hook will warm: the current one, then forward until the accumulated hold time
 * covers the lookahead. Always at least one slot ahead, so a single very long slide still warms its
 * successor. */
function windowFrom(slots: KioskSlot[], slotIndex: number): number[] {
  const indices: number[] = [];
  if (slots.length === 0) return indices;
  const current = slotIndex % slots.length;
  indices.push(current);
  let covered = 0;
  for (let step = 1; step <= MAX_LOOKAHEAD_SLOTS; step += 1) {
    const index = (slotIndex + step) % slots.length;
    indices.push(index);
    const slot = slots[index];
    covered += slot ? holdMsOf(slot) : 0;
    if (covered >= LOOKAHEAD_MS) break;
  }
  return Array.from(new Set(indices));
}

/* ------------------------------------------------------------------ readiness bookkeeping
 *
 * Module-scoped rather than component state, because the answer is a property of the bytes and not of
 * a render: two hooks asking about the same slide must get the same answer, and a slide already warmed
 * before a playlist revision must not be re-awaited after it. */

const readyMedia = new Set<string>();
const readyReels = new Set<string>();
const inflight = new Set<string>();

function warmOneMedia(
  eventId: string,
  mediaId: string,
  needsAuth: boolean,
  onDone: () => void
): void {
  const key = cacheKeyFor(eventId, mediaId, "display");
  if (readyMedia.has(key) || inflight.has(key)) return;
  inflight.add(key);

  const finish = () => {
    inflight.delete(key);
    readyMedia.add(key);
    onDone();
  };

  // The document and the pixels are independent round trips; a slide is ready only once it holds both.
  const docTask = prefetchMediaDoc(eventId, mediaId);
  const bytesTask = needsAuth
    ? // Resolve the signed URL, then pull the bytes into `lib/mediaUrls.ts`'s durable store — the same
      // store `useMediaSrc` reads first, so a warmed slide paints on its first render.
      resolveSignedUrl(mediaRenderPath(eventId, mediaId, "display")).then((url) =>
        url ? cacheBytes(key, url).then((blob) => blob !== null) : false
      )
    : warmHttpCache(mediaRenderUrl(eventId, mediaId, "display"));

  void Promise.all([docTask, bytesTask]).then(finish, finish);
}

function warmOneReel(
  eventId: string,
  reelId: string,
  needsAuth: boolean,
  onDone: () => void
): void {
  const key = `${eventId}:reel:${reelId}`;
  if (readyReels.has(key) || inflight.has(key)) return;
  inflight.add(key);
  const finish = () => {
    inflight.delete(key);
    readyReels.add(key);
    onDone();
  };

  // The reel *document* is the round trip worth pre-paying: `videoUri` lives on it, so until it lands
  // the wall cannot start fetching video at all — it can only show the title card and wait, which is
  // what a black premiere looks like. On an invite-only event the redirect hop is warmed too, and
  // `resolveSignedUrl` memoises the answer until shortly before it expires, so the `<video>` that
  // mounts later reads it from that cache instead of making the call on screen.
  //
  // The MP4 bytes are still deliberately not downloaded here: tens of megabytes for a slide that may
  // never be reached. `ReelSlot` sets `preload="auto"` and starts on `canplaythrough`, so the buffering
  // happens behind the title card by design rather than as a stall.
  void prefetchReelDoc(eventId, reelId)
    .then(async (reel) => {
      if (needsAuth && reel?.videoUri) await resolveSignedUrl(reel.videoUri);
    })
    .then(finish, finish);
}

/** Whether everything slot `index` needs is on hand. A slot with no photographic bytes (`leaderboard`,
 * `collage`, `bounty_call`) is ready by definition. */
function slotIsReady(eventId: string, slot: KioskSlot | undefined): boolean {
  if (!slot) return true;
  if (slot.type === "reel") return readyReels.has(`${eventId}:reel:${slot.reelId}`);
  const ids = mediaIdsOf(slot);
  if (ids.length === 0) return true;
  // A filmstrip is ready when its *first* frame is: the rest fill in behind the transition, and holding
  // the wall for the sixth thumbnail of a strip would be the timer wagging the show.
  return readyMedia.has(cacheKeyFor(eventId, ids[0], "display"));
}

export interface SlotPrefetch {
  /** True once the current slot's bytes and document are on hand — or once the wall has waited long
   * enough that showing it regardless is the better failure. `KioskShow` holds its first paint on this. */
  currentReady: boolean;
  /** Ask about any index, so the advance can refuse to walk into a cold slide. Reads the same
   * module-level bookkeeping, so it never lags a render behind the truth. */
  isReady: (index: number) => boolean;
}

/**
 * Warm the next ~10 seconds of the programme, and report what is ready. Call once from the kiosk shell.
 *
 * `needsAuth` mirrors `MediaImg`'s own choice exactly — it must, or the prefetch warms one path while
 * the renderer reads the other and the whole thing silently does nothing. Pass `undefined` while the
 * event bootstrap is still in flight and this no-ops rather than guessing: one slide's worth of latency
 * is cheaper than warming ~50 wrong URLs.
 */
export function useSlotPrefetch(
  eventId: string,
  slots: KioskSlot[],
  slotIndex: number,
  needsAuth: boolean | undefined,
  revision: number | undefined
): SlotPrefetch {
  // A counter rather than a boolean: every completed warm bumps it, which re-renders the consumer so
  // it can re-ask `isReady` about whichever index it cares about now.
  const [, bump] = useState(0);
  const onDone = useCallback(() => bump((n) => n + 1), []);
  const [timedOut, setTimedOut] = useState(false);
  const slotsRef = useRef(slots);
  slotsRef.current = slots;

  useEffect(() => {
    if (needsAuth === undefined || slots.length === 0) return;
    for (const index of windowFrom(slots, slotIndex)) {
      const slot = slots[index];
      if (!slot) continue;
      if (slot.type === "reel") {
        warmOneReel(eventId, slot.reelId, needsAuth, onDone);
        continue;
      }
      for (const mediaId of mediaIdsOf(slot)) {
        warmOneMedia(eventId, mediaId, needsAuth, onDone);
      }
    }
    // `revision` is in the deps because a rewritten programme is a different set of slides even at the
    // same index — and the publisher only writes a revision when its *decisions* changed (S8a), so this
    // cannot spin on the recency-decay rebuilds that leave the programme identical.
  }, [eventId, slots, slotIndex, needsAuth, revision, onDone]);

  // The backstop. Re-armed per slot, so one unavailable photograph costs one slide's patience rather
  // than disabling the gate for the rest of the evening.
  useEffect(() => {
    setTimedOut(false);
    const t = setTimeout(() => setTimedOut(true), READY_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [slotIndex, revision]);

  const isReady = useCallback(
    (index: number) => {
      const list = slotsRef.current;
      if (list.length === 0) return false;
      return slotIsReady(eventId, list[index % list.length]);
    },
    [eventId]
  );

  const currentReady = slots.length === 0 ? false : timedOut || isReady(slotIndex);
  return { currentReady, isReady };
}
