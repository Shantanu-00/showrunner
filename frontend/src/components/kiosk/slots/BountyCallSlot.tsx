"use client";

import { useEffect, useState } from "react";
import type { BountyCallSlot as BountyCallSlotType, BountyDoc } from "@/lib/types";
import { listenBounty } from "@/lib/firestore";
import { JoinQr } from "../JoinQr";
import { joinUrl } from "@/lib/kiosk";

/** `bounty_call` — the wanted poster (spec 12 §6): The Twist commanding the room, full-screen
 * in the active stage accent. */
export function BountyCallSlot({ eventId, slot }: { eventId: string; slot: BountyCallSlotType }) {
  const [bounty, setBounty] = useState<BountyDoc | null>(null);

  useEffect(() => {
    return listenBounty(eventId, slot.bountyId, setBounty, () => setBounty(null));
  }, [eventId, slot.bountyId]);

  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-center px-[8%] text-center"
      style={{ background: "var(--accent-2, var(--maroon-700))" }}
    >
      <p className="font-mono text-sm tracking-[0.25em] mb-4" style={{ color: "var(--gold-300)" }}>
        THE DIRECTOR NEEDS
      </p>
      <p
        className="font-[var(--font-display)] mb-6"
        style={{ color: "var(--ivory)", fontSize: "min(8vw, 61px)" }}
      >
        {bounty?.ask ?? "the next great shot"}
      </p>
      {bounty && (
        <span
          className="inline-block px-5 py-2 rounded-[var(--radius-pill)] font-mono text-lg mb-8"
          style={{ background: "var(--gold-500)", color: "var(--bg-0)" }}
        >
          +{bounty.pointsAward}
        </span>
      )}
      <JoinQr url={joinUrl(eventId)} sizePx={180} />
    </div>
  );
}
