"use client";

import type { EventPublicInfo } from "@/lib/types";
import { PublicGallery } from "./PublicGallery";

/** Thin wrapper: the Event tab is the public gallery (spec 12 §5.2 point 3) — reels row and
 * missions chip land with the bounty/reel sessions (S8/S10), not this one. */
export function EventTab({
  eventId,
  eventInfo,
  judgeMode,
}: {
  eventId: string;
  eventInfo: EventPublicInfo | null;
  judgeMode: boolean;
}) {
  return <PublicGallery eventId={eventId} stages={eventInfo?.stages ?? []} judgeMode={judgeMode} />;
}
