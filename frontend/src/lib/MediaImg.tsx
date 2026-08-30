"use client";

// One `<img>` that knows which of the two byte paths this event uses.
//
// `api/media.py` serves a public, fully-indexed photo with no Authorization header on an **open**
// event — that is what lets a masonry grid or a kiosk filmstrip be plain image requests — and requires
// event membership on every branch of an **invite-only** one. An `<img src>` cannot carry a bearer
// token, so on an invite-only event the bytes have to come through `useAuthedImage`: authed fetch →
// follow the 302 → blob URL. That was already built for the pool/self tiers (a private album's
// thumbnails were never public), so closing the door needed **no token scheme, no signed query
// parameter** — just this component choosing between two paths that both already existed.
//
// It is a component rather than a hook because the surfaces that need it render inside `.map()`
// callbacks (`PublicGallery`'s grid, `JustInSlot`'s filmstrip) where a hook cannot be called at all.
// `AlbumGrid`/`MyUploads` already use `useAuthedImage` directly in their own per-tile components and
// are unchanged: those tiers are never public, so there is nothing to choose between.

import type { CSSProperties, ReactNode } from "react";
import { mediaRenderUrl } from "./api";
import { useAccessMode } from "./eventAccess";
import { useAuthedImage } from "./useAuthedImage";

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
}) {
  const mode = useAccessMode(eventId);
  // `undefined` (the bootstrap has not answered yet) takes the authed path on purpose: it works on
  // both kinds of event, whereas the unauthenticated path works on only one. The cost is one wasted
  // fetch during the ~100 ms before `accessMode` lands; the alternative is a private event's guests
  // looking at broken images. See the fail-closed paragraph in `lib/eventAccess.ts`.
  const needsAuth = forceAuthed || mode !== "open";
  const authedSrc = useAuthedImage(eventId, needsAuth ? mediaId : null, variant);
  const src = !mediaId ? null : needsAuth ? authedSrc : mediaRenderUrl(eventId, mediaId, variant);

  if (!src) return <>{fallback}</>;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={imgKey ?? mediaId ?? undefined}
      src={src}
      alt={alt}
      className={className}
      style={style}
      onLoad={onLoad}
    />
  );
}
