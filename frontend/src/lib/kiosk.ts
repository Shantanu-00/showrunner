import type { KioskSlot } from "./types";

/** Hold duration per slot type (spec 04 §4 / spec 12 §6). Only `hero` carries its own
 * `holdSec` from the publisher; the others rotate on the fixed cadence the spec states
 * (leaderboard "every ~90s... 8s hold", collage/just_in/bounty_call are un-timed in the spec
 * text so a steady, legible default is used). */
export function slotHoldSec(slot: KioskSlot): number {
  switch (slot.type) {
    case "hero":
      return slot.holdSec;
    case "leaderboard":
      return 8;
    case "bounty_call":
      return 10;
    case "collage":
      return 6;
    case "just_in":
      return 8;
    case "reel":
      return 0; // the premiere storyboard owns its own timing
  }
}

export function joinUrl(eventId: string): string {
  if (typeof window === "undefined") return `/join/${eventId}`;
  return `${window.location.origin}/join/${eventId}`;
}
