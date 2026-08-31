"use client";

// A small, bounded blob-URL cache shared by the kiosk's prefetcher and its renderer.
//
// It exists because of a specific defect: the kiosk's *decisions* were always ~5 minutes ahead (the
// publisher writes a whole `KIOSK_PROGRAM_SECONDS` playlist of slots), but its **bytes** were not.
// Each slide mounted, *then* started fetching — API render endpoint → 302 → GCS — so every single
// transition paid a visible round trip on screen, and the caption reveal's 1.5 s backstop kept firing
// before its own photograph had painted. The director was never the bottleneck; nothing was preloaded.
//
// Prefetching the *open-event* path needs no cache at all: `new Image().src = url` warms the browser's
// own HTTP cache and the later `<img>` hits it. The **invite-only** path cannot work that way —
// `api/media.py` requires a bearer token there, an `<img src>` cannot send one, so those bytes arrive
// through `authedFetch` → blob URL, and a blob URL is per-call state that no browser cache can share.
// Without something like this file, prefetching an invite-only kiosk would fetch every photo twice and
// still paint late. Hence one store both sides address by API path.
//
// Two deliberate constraints:
//
// - **Bounded, with eviction that actually revokes.** Blob URLs pin their blob in memory until
//   revoked, and a kiosk is a page that runs for five days. `MAX_ENTRIES` keeps the working set to a
//   few screens of photographs; evicting the least-recently-used entry revokes it.
// - **The cache owns the lifetime, not the component.** A consumer of a shared entry must never
//   revoke on unmount — the next slide may be showing the same photograph, and revoking a URL another
//   `<img>` still points at breaks it instantly and silently. This is exactly why `useAuthedBlobUrl`
//   keeps its original non-shared behaviour by default and opts in per caller: the video player in
//   the wrap panel should still revoke its (large, single-use) blob, and does.

import { authedFetch } from "./api";

/** ~48 photographs. Comfortably more than the deepest prefetch window plus a full hero rotation, and
 * small enough that a long-running kiosk's memory stays flat. */
const MAX_ENTRIES = 48;

/** `apiPath → blobUrl`. A `Map` for its insertion-order guarantee: re-inserting on read makes the
 * first key the least-recently-used one, which is the whole eviction policy in one line. */
const entries = new Map<string, string>();

/** In-flight fetches, so a prefetch and a render racing for the same photo make one request. */
const inflight = new Map<string, Promise<string | null>>();

function touch(path: string, url: string): void {
  entries.delete(path);
  entries.set(path, url);
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.keys().next();
    if (oldest.done) break;
    const evicted = entries.get(oldest.value);
    entries.delete(oldest.value);
    if (evicted) URL.revokeObjectURL(evicted);
  }
}

/** The cached blob URL for `path`, or null. Synchronous — this is what lets a prefetched slide paint
 * on its first render instead of one state update later. */
export function cachedBlob(path: string | null | undefined): string | null {
  if (!path) return null;
  const hit = entries.get(path);
  if (hit) touch(path, hit);
  return hit ?? null;
}

/** Fetch `path` with a bearer token and cache the resulting blob URL. Idempotent and safe to call
 * repeatedly: a hit returns immediately, a concurrent call joins the in-flight promise.
 *
 * Never throws. A failed prefetch is a slide that loads at its normal speed, which is precisely the
 * behaviour this whole file is improving on — not an error worth surfacing to a room. */
export async function warmBlob(path: string | null | undefined): Promise<string | null> {
  if (!path) return null;
  const hit = cachedBlob(path);
  if (hit) return hit;
  const pending = inflight.get(path);
  if (pending) return pending;

  const task = (async () => {
    try {
      const res = await authedFetch(path, { method: "GET" });
      if (!res.ok) return null;
      const url = URL.createObjectURL(await res.blob());
      touch(path, url);
      return url;
    } catch {
      return null;
    } finally {
      inflight.delete(path);
    }
  })();
  inflight.set(path, task);
  return task;
}

/** Warm an unauthenticated render URL through the browser's own HTTP cache. No blob, no bookkeeping:
 * the later `<img src>` with the same URL is a cache hit. `decode()` is awaited when available so the
 * image is not merely downloaded but *decoded* before the slide needs it — on a 4K kiosk panel,
 * decoding a 1600px JPEG is itself long enough to see. */
export function warmImage(url: string): void {
  if (typeof window === "undefined") return;
  const img = new Image();
  img.decoding = "async";
  img.src = url;
  void img.decode?.().catch(() => {});
}
