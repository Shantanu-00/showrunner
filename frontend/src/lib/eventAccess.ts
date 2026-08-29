"use client";

// Which events are invite-only, as far as this browser tab knows.
//
// One fact, needed in two very different places. `api/media.py` serves a public, fully-indexed photo
// with **no** Authorization header on an *open* event — that is what lets a kiosk put 60 thumbnails in
// a masonry grid without 60 authed round trips — and refuses that same branch on an *invite-only*
// event. So the client has to know which kind of event it is looking at before it builds an `<img
// src>`, or it renders a grid of broken images. The answer arrives on
// `GET /v1/events/{eventId}/public` as `accessMode`, which every surface already fetches on load.
//
// A module-level store with a subscription rather than prop-drilling through four component trees:
// `MediaImg` is rendered inside `.map()` callbacks (the public gallery's masonry, the kiosk's just-in
// filmstrip) where there is no sensible place to thread a prop from, and `useSyncExternalStore` is
// exactly the React primitive for "an external mutable value a component must re-render on".
//
// **Unknown is treated as invite-only, deliberately.** The authed path works on both kinds of event —
// `api/media.py`'s public branch ignores a token it does not need — whereas the unauthenticated path
// works on only one. So guessing "authed" costs a wasted fetch on an open event for the ~100 ms before
// `accessMode` lands, and guessing "open" would show a private event's guests broken images. In
// practice the mode is known before the first photo is: `getEventPublic` is one HTTP request started
// on the same page load as the Firestore listener that has to complete a websocket handshake and a
// query before it can hand back a mediaId to render.

import { useSyncExternalStore } from "react";

export type AccessMode = "open" | "invite";

const modes = new Map<string, AccessMode>();
const listeners = new Set<() => void>();

/** Called by `getEventPublic` (`lib/api.ts`) on every fetch of the event bootstrap. Idempotent, and a
 * no-op when the value has not changed — a redundant notify would re-render every image on the wall. */
export function recordAccessMode(eventId: string, mode: string | undefined): void {
  const next: AccessMode | undefined = mode === "invite" ? "invite" : mode === "open" ? "open" : undefined;
  if (!next || modes.get(eventId) === next) return;
  modes.set(eventId, next);
  listeners.forEach((fn) => fn());
}

/** `undefined` until `GET /v1/events/{id}/public` has answered. */
export function getAccessMode(eventId: string): AccessMode | undefined {
  return modes.get(eventId);
}

/** True when a photo's bytes need a bearer token — i.e. on an invite-only event, and while the mode is
 * still unknown. See the "unknown is treated as invite-only" paragraph above. */
export function bytesNeedAuth(eventId: string): boolean {
  return modes.get(eventId) !== "open";
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

/** Re-renders the caller when this event's access mode is learned or changed. */
export function useAccessMode(eventId: string): AccessMode | undefined {
  return useSyncExternalStore(
    subscribe,
    () => modes.get(eventId),
    // Server snapshot: a static export prerenders these components, and there is no event bootstrap
    // during the build. `undefined` reads as "not open yet", which is the fail-closed answer.
    () => undefined
  );
}
