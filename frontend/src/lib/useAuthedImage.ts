"use client";

import { useEffect, useState } from "react";
import { authedFetch, mediaRenderPath } from "./api";

/** Any media the browser must fetch with a bearer token, as a local blob URL.
 *
 * `<img src>` and `<video src>` cannot carry an Authorization header, so this fetches the API's render
 * redirect with `authedFetch`, follows it to the signed GCS URL (the browser drops `Authorization` on
 * that cross-origin hop per the fetch spec, which is fine: the signed URL authenticates itself), and
 * hands back a blob URL. `null` skips the fetch entirely, which is how callers switch this off.
 *
 * Two situations need it, and they were once one: a pool/self-tier photo is never public and always
 * needed a token, and now *every* item on an invite-only event does — `api/media.py` and
 * `api/reels.py` stop serving bytes unauthenticated once the host shuts the door. That second case is
 * why closing the door needed no token scheme and no signed query parameter: this path already existed
 * and already worked. */
export function useAuthedBlobUrl(url: string | null | undefined): string | null {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setSrc(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;

    authedFetch(url, { method: "GET" })
      .then(async (res) => {
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  return src;
}

/** The photo case: `useAuthedBlobUrl` over `…/media/{id}/render`. Callers that also have to handle the
 * *public* tier of an open event should use `MediaImg`, which picks between this and a bare
 * `<img src>` from the event's access mode. */
export function useAuthedImage(
  eventId: string,
  mediaId: string | null | undefined,
  variant: "thumb" | "display"
): string | null {
  return useAuthedBlobUrl(mediaId ? mediaRenderPath(eventId, mediaId, variant) : null);
}
