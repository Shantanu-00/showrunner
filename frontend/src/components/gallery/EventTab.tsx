"use client";

import { useEffect, useState } from "react";
import { Trophy, Target } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";
import { PublicGallery } from "./PublicGallery";
import { MissionsSheet } from "./MissionsSheet";
import { LeaderboardSheet } from "./LeaderboardSheet";

export function EventTab({
  eventId,
  eventInfo,
  explainMode,
  onShootNow,
}: {
  eventId: string;
  eventInfo: EventPublicInfo | null;
  explainMode: boolean;
  onShootNow: (bountyId: string) => void;
}) {
  const [missionCount, setMissionCount] = useState(0);
  const [sheet, setSheet] = useState<"none" | "missions" | "leaderboard">("none");

  useEffect(
    () => listenActiveBounties(eventId, (items) => setMissionCount(items.length), () => setMissionCount(0)),
    [eventId]
  );

  return (
    <section className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between px-4 pb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono uppercase tracking-wider text-[var(--accent)]">
            Curated Stream
          </span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {missionCount > 0 && (
            <button
              type="button"
              onClick={() => setSheet("missions")}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-[var(--gold-500)]/10 text-[var(--accent)] border border-[var(--gold-500)]/30 hover:bg-[var(--gold-500)]/20 transition-all active:scale-95 font-medium"
            >
              <Target className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>{missionCount} active missions</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => setSheet("leaderboard")}
            aria-label="Leaderboard"
            className="flex items-center justify-center p-2 rounded-full glass-card hover:border-[var(--accent)] text-[var(--gold-300)] transition-all active:scale-95"
            title="Leaderboard"
          >
            <Trophy className="w-4 h-4 stroke-[2]" />
          </button>
        </div>
      </div>

      <PublicGallery eventId={eventId} stages={eventInfo?.stages ?? []} explainMode={explainMode} />

      {sheet === "missions" && (
        <MissionsSheet eventId={eventId} onShootNow={onShootNow} onClose={() => setSheet("none")} />
      )}
      {sheet === "leaderboard" && (
        <LeaderboardSheet eventId={eventId} onClose={() => setSheet("none")} />
      )}
    </section>
  );
}
