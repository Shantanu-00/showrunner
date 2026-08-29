"use client";

import { useEffect, useState } from "react";
import { ensureMembership } from "@/lib/membership";
import { getEventPublic } from "@/lib/api";
import type { EventPublicInfo } from "@/lib/types";
import { useRouteEventId } from "@/lib/routeParams";
import { KioskSetup } from "./KioskSetup";
import { KioskShow } from "./KioskShow";

/** `/kiosk/{eventId}` (spec 12 §5.3). Every client, kiosk included, signs in anonymously and joins the
 * event on load: `isMember(eventId)` in `firestore.rules` is a claim check, so media, people, guests,
 * bounty and reel reads are all denied until `POST /join` has minted it.
 *
 * On an invite-only event the TV needs a code, which arrives as `?joinCode=` on a kiosk link the host
 * mints from the console (`POST /v1/events/{eventId}/kiosk-links` — it grants `members`, never `hosts`,
 * so a link left on a screen in a function hall is not a route to a console). `ensureMembership` reads
 * it out of the URL and strips it. Without membership the wall renders an empty programme rather than
 * an error, which is the right failure for a television: the playlist document is still world-readable
 * (spec 09 §3), it just resolves to nothing. */
export function KioskShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/kiosk/", fallbackEventId);
  const [authReady, setAuthReady] = useState(false);
  const [eventInfo, setEventInfo] = useState<EventPublicInfo | null>(null);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    // Deliberately not gated on the outcome: a kiosk that refuses to start because a join failed is a
    // dark wall, and a dark wall is worse than an empty one. A non-member simply renders nothing.
    void ensureMembership(eventId).then(() => setAuthReady(true));
  }, [eventId]);

  useEffect(() => {
    if (!authReady) return;
    void getEventPublic(eventId).then(setEventInfo, () => {});
  }, [eventId, authReady]);

  // Spec 12 §3: pure CSS-variable retune, no reload — the kiosk's own stage-change re-theme
  // (≤5s acceptance) additionally happens live via KioskShow's playlist listener.
  useEffect(() => {
    if (eventInfo?.templateId) document.documentElement.dataset.theme = eventInfo.templateId;
    if (eventInfo?.activeStage) document.documentElement.dataset.stage = eventInfo.activeStage;
  }, [eventInfo?.templateId, eventInfo?.activeStage]);

  if (!started) {
    return <KioskSetup eventInfo={eventInfo} onStart={() => setStarted(true)} />;
  }
  return <KioskShow eventId={eventId} eventInfo={eventInfo} />;
}
