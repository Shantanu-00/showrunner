"use client";

import { useEffect, useState } from "react";
import { Trophy, Target, Sparkles } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";
import { useHaptics } from "@/lib/useHaptics";
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
  const { tapHaptic } = useHaptics();

  useEffect(
    () => listenActiveBounties(eventId, (items) => setMissionCount(items.length), () => setMissionCount(0)),
    [eventId]
  );

  const openSheet = (type: "missions" | "leaderboard") => {
    tapHaptic();
    setSheet(type);
  };

  return (
    <section className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between px-4 pb-3">
        <div className="flex items-center gap-2">
          <span className="p-1 rounded-md bg-[var(--accent)]/15 text-[var(--accent)]">
            <Sparkles className="w-3.5 h-3.5" />
          </span>
          <span className="text-xs font-mono uppercase tracking-wider text-[var(--accent)] font-semibold">
            Live Stream
          </span>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {missionCount > 0 && (
            <button
              type="button"
              onClick={() => openSheet("missions")}
              className="flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] border border-[var(--accent)]/30 hover:bg-[var(--accent)]/25 transition-all active:scale-95 font-medium cursor-pointer shadow-md font-mono tabular-nums"
            >
              <Target className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>{missionCount} active missions</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => openSheet("leaderboard")}
            aria-label="Leaderboard"
            className="flex items-center justify-center p-2 rounded-full glass-card hover:border-[var(--accent)] text-[var(--text-primary)] transition-all active:scale-95 cursor-pointer shadow-md"
            title="Leaderboard"
          >
            <Trophy className="w-4 h-4 text-amber-400 stroke-[2]" />
          </button>
        </div>
      </div>

      <PublicGallery
        eventId={eventId}
        stages={eventInfo?.stages ?? []}
        activeStageId={eventInfo?.activeStage ?? null}
        explainMode={explainMode}
      />

      {sheet === "missions" && (
        <MissionsSheet eventId={eventId} onShootNow={onShootNow} onClose={() => setSheet("none")} />
      )}
      {sheet === "leaderboard" && (
        <LeaderboardSheet eventId={eventId} onClose={() => setSheet("none")} />
      )}
    </section>
  );
}
