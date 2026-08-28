"use client";

import { useEffect, useState } from "react";
import type { EventPublicInfo } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";
import { PublicGallery } from "./PublicGallery";
import { MissionsSheet } from "./MissionsSheet";
import { LeaderboardSheet } from "./LeaderboardSheet";

/** The Event tab (spec 12 §5.2 point 3): top bar (event name, 🏆 leaderboard entry, 🎯 missions
 * chip when any bounty is live) over the public gallery. Reels row lands with the Reel Director
 * session, not this one (spec 12 §5.2 names it as a separate, still-unbuilt row). */
export function EventTab({
  eventId,
  eventInfo,
  judgeMode,
  onShootNow,
}: {
  eventId: string;
  eventInfo: EventPublicInfo | null;
  judgeMode: boolean;
  onShootNow: (bountyId: string) => void;
}) {
  const [missionCount, setMissionCount] = useState(0);
  const [sheet, setSheet] = useState<"none" | "missions" | "leaderboard">("none");

  useEffect(
    () => listenActiveBounties(eventId, (items) => setMissionCount(items.length), () => setMissionCount(0)),
    [eventId]
  );

  return (
    <section>
      <div className="flex items-center justify-between px-4 pb-3">
        <p className="font-[var(--font-display)] text-lg truncate" style={{ color: "var(--ivory)" }}>
          {eventInfo?.name ?? ""}
        </p>
        <div className="flex items-center gap-2 shrink-0">
          {missionCount > 0 && (
            <button
              type="button"
              onClick={() => setSheet("missions")}
              className="text-sm px-3 py-1.5 rounded-[var(--radius-pill)]"
              style={{ border: "var(--hairline)", color: "var(--accent)" }}
            >
              🎯 {missionCount} active
            </button>
          )}
          <button
            type="button"
            onClick={() => setSheet("leaderboard")}
            aria-label="Leaderboard"
            className="text-lg px-2 py-1"
          >
            🏆
          </button>
        </div>
      </div>

      <PublicGallery eventId={eventId} stages={eventInfo?.stages ?? []} judgeMode={judgeMode} />

      {sheet === "missions" && (
        <MissionsSheet eventId={eventId} onShootNow={onShootNow} onClose={() => setSheet("none")} />
      )}
      {sheet === "leaderboard" && (
        <LeaderboardSheet eventId={eventId} onClose={() => setSheet("none")} />
      )}
    </section>
  );
}
