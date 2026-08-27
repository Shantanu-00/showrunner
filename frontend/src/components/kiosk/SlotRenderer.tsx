"use client";

import type { KioskSlot } from "@/lib/types";
import { HeroSlot } from "./slots/HeroSlot";
import { JustInSlot } from "./slots/JustInSlot";
import { CollageSlot } from "./slots/CollageSlot";
import { LeaderboardSlot } from "./slots/LeaderboardSlot";
import { BountyCallSlot } from "./slots/BountyCallSlot";
import { ReelSlot } from "./slots/ReelSlot";

export function SlotRenderer({ eventId, slot }: { eventId: string; slot: KioskSlot }) {
  switch (slot.type) {
    case "hero":
      return <HeroSlot eventId={eventId} slot={slot} />;
    case "just_in":
      return <JustInSlot eventId={eventId} slot={slot} />;
    case "collage":
      return <CollageSlot />;
    case "leaderboard":
      return <LeaderboardSlot eventId={eventId} slot={slot} />;
    case "bounty_call":
      return <BountyCallSlot eventId={eventId} slot={slot} />;
    case "reel":
      return <ReelSlot eventId={eventId} slot={slot} />;
  }
}
