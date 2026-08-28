"use client";

import { useEffect, useState } from "react";
import { authedFetch, mediaRenderPath } from "./api";

/** Pool/self-tier photos (private album, "My uploads") need a bearer token, which an `<img src>`
 * cannot carry — so this fetches the render redirect with `authedFetch`, follows it to the signed
 * GCS URL (the browser drops `Authorization` on that cross-origin hop per the fetch spec, which is
 * fine: the signed URL authenticates itself), and hands back a local blob URL. Public-tier surfaces
 * don't need this — `mediaRenderUrl` goes straight in `<img src>` for those. */
export function useAuthedImage(
  eventId: string,
  mediaId: string | null | undefined,
  variant: "thumb" | "display"
): string | null {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!mediaId) {
      setSrc(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;

    authedFetch(mediaRenderPath(eventId, mediaId, variant), { method: "GET" })
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
  }, [eventId, mediaId, variant]);

  return src;
}
