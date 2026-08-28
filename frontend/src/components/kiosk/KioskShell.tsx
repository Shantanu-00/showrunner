"use client";

import { useEffect, useState } from "react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { getEventPublic } from "@/lib/api";
import type { EventPublicInfo } from "@/lib/types";
import { useRouteEventId } from "@/lib/routeParams";
import { KioskSetup } from "./KioskSetup";
import { KioskShow } from "./KioskShow";

/** `/kiosk/{eventId}` (spec 12 §5.3). Every client, kiosk included, signs in anonymously on
 * load — media-doc reads require an authed event member (spec 09 §3 rules). */
export function KioskShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/kiosk/", fallbackEventId);
  const [authReady, setAuthReady] = useState(false);
  const [eventInfo, setEventInfo] = useState<EventPublicInfo | null>(null);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    void ensureAnonymousAuth().then(() => setAuthReady(true));
  }, []);

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
