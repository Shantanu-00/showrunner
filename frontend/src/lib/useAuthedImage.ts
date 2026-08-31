"use client";

import { useEffect, useState } from "react";
import { mediaRenderPath, mediaRenderUrl } from "./api";
import {
  cacheBytes,
  cacheKeyFor,
  cachedObjectUrl,
  hydrateFromCache,
  resolveSignedUrl,
} from "./mediaUrls";

/** A `src` for one piece of media, resolved through `lib/mediaUrls.ts`'s memory → IndexedDB →
 * signed-URL ladder.
 *
 * This used to hand back a blob URL produced by `authedFetch` following the render endpoint's 302,
 * and on every surface that needed a token that fetch was **failing outright** — a preflighted
 * request cannot survive a redirect onto a bucket, so a private album rendered as a grid of
 * shimmering rectangles. It now asks the API for the signed URL (`?json=1`) and returns *that*: an
 * `<img src>` needs no header, so the byte hop stops being a CORS request at all.
 *
 * The second change is that repeat views cost nothing. Bytes this device has already downloaded come
 * back from IndexedDB — synchronously when the tab is still warm — so switching tabs, reopening the
 * PWA, or scrolling back up shows photographs instead of skeletons. */
export function useMediaSrc(
  eventId: string,
  mediaId: string | null | undefined,
  variant: "thumb" | "display",
  /** Send a token when resolving the URL. Required for every ring above `public` and for everything
   * on an invite-only event; `MediaImg` decides it from the event's access mode. */
  authed: boolean
): string | null {
  const key = mediaId ? cacheKeyFor(eventId, mediaId, variant) : null;
  const [src, setSrc] = useState<string | null>(() => cachedObjectUrl(key));

  useEffect(() => {
    if (!mediaId || !key) {
      setSrc(null);
      return;
    }
    let cancelled = false;

    const warm = cachedObjectUrl(key);
    if (warm) {
      setSrc(warm);
      return;
    }

    void (async () => {
      const stored = await hydrateFromCache(key);
      if (cancelled) return;
      if (stored) {
        setSrc(stored);
        return;
      }
      const url = authed
        ? await resolveSignedUrl(mediaRenderPath(eventId, mediaId, variant))
        : mediaRenderUrl(eventId, mediaId, variant);
      if (cancelled) return;
      setSrc(url);
      // Keep a copy for next time, but never swap the element's `src` to the blob afterwards: the
      // photograph is already painting, and re-pointing it would decode the same image twice.
      if (url) void cacheBytes(key, url);
    })();

    return () => {
      cancelled = true;
    };
  }, [eventId, mediaId, variant, authed, key]);

  return src;
}

/** The historical name, kept because several tile components call it directly. Always authed — those
 * surfaces (a private album, "My uploads") are never public on either kind of event. */
export function useAuthedImage(
  eventId: string,
  mediaId: string | null | undefined,
  variant: "thumb" | "display"
): string | null {
  return useMediaSrc(eventId, mediaId, variant, true);
}

/** Any other authed media path — today that means a reel's `…/video`, for a `<video src>`.
 *
 * Same fix, same reason: the redirect could not be followed with a token on it, so this resolves the
 * URL instead of the bytes. No blob, so nothing to revoke and nothing held in memory — a recap film
 * is tens of megabytes and was previously buffered whole before the first frame appeared. */
export function useAuthedBlobUrl(url: string | null | undefined): string | null {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    void resolveSignedUrl(url).then((resolved) => {
      if (!cancelled) setSrc(resolved);
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return src;
}
