"use client";

import { useEffect, useState } from "react";

/** Static export bakes one HTML file per dynamic route at build time (the default event id);
 * firebase.json rewrites every other event's URL under the same prefix to that same file
 * (friction-log 08-27). This recovers the real eventId from the browser's URL so any event —
 * not just the build-time default — resolves correctly once the client takes over. */
export function useRouteEventId(prefix: string, fallback: string): string {
  const [eventId, setEventId] = useState(fallback);
  useEffect(() => {
    const path = window.location.pathname;
    if (!path.startsWith(prefix)) return;
    const seg = path.slice(prefix.length).split("/").filter(Boolean)[0];
    if (seg) setEventId(decodeURIComponent(seg));
  }, [prefix]);
  return eventId;
}
