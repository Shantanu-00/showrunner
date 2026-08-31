"use client";

// One `<img>` that knows which of the two byte paths this event uses.
//
// `api/media.py` serves a public, fully-indexed photo with no Authorization header on an **open**
// event — that is what lets a masonry grid or a kiosk filmstrip be plain image requests — and requires
// event membership on every branch of an **invite-only** one. An `<img src>` cannot carry a bearer
// token, so on an invite-only event the URL has to be resolved first (`?json=1` → signed URL), which
// is what `useMediaSrc` does. Either way what lands here is a URL an `<img>` can load directly;
// nothing on this path fetches image bytes through a header any more, because that could not work
// (see `lib/mediaUrls.ts`).
//
// It is a component rather than a hook because the surfaces that need it render inside `.map()`
// callbacks (`PublicGallery`'s grid, `JustInSlot`'s filmstrip) where a hook cannot be called at all.

import type { CSSProperties, ReactNode } from "react";
import { useAccessMode } from "./eventAccess";
import { useMediaSrc } from "./useAuthedImage";

export function MediaImg({
  eventId,
  mediaId,
  variant,
  alt = "",
  className,
  style,
  fallback = null,
  /** Set for a photo whose `visibility` is not `public` (a private album, "My uploads"): those need a
   * token on *either* kind of event, so the access mode is irrelevant to them. */
  forceAuthed = false,
  imgKey,
  onLoad,
  /** Off for the handful of images that are the reason the page exists (a kiosk slide, a lightbox);
   * on for everything in a scrolling grid. */
  eager = false,
}: {
  eventId: string;
  mediaId: string | null | undefined;
  variant: "thumb" | "display";
  alt?: string;
  className?: string;
  style?: CSSProperties;
  fallback?: ReactNode;
  forceAuthed?: boolean;
  imgKey?: string;
  onLoad?: () => void;
  eager?: boolean;
}) {
  const mode = useAccessMode(eventId);
  // `undefined` (the bootstrap has not answered yet) takes the authed path on purpose: it works on
  // both kinds of event, whereas the unauthenticated path works on only one. See the fail-closed
  // paragraph in `lib/eventAccess.ts`.
  const needsAuth = forceAuthed || mode !== "open";
  const src = useMediaSrc(eventId, mediaId, variant, needsAuth);

  if (!src) return <>{fallback}</>;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={imgKey ?? mediaId ?? undefined}
      src={src}
      alt={alt}
      className={className}
      style={style}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      onLoad={onLoad}
    />
  );
}
