"use client";

import { useEffect, useState } from "react";
import { Trophy, Target, CalendarDays } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";
import { listenActiveBounties } from "@/lib/firestore";
import { dayLabelFromIndex } from "@/lib/eventTime";
import { useHaptics } from "@/lib/useHaptics";
import { PublicGallery } from "./PublicGallery";
import { MissionsSheet } from "./MissionsSheet";
import { LeaderboardSheet } from "./LeaderboardSheet";
import { TimelineSheet } from "./TimelineSheet";

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
  const [sheet, setSheet] = useState<"none" | "missions" | "leaderboard" | "timeline">("none");
  const { tapHaptic } = useHaptics();

  useEffect(
    () => listenActiveBounties(eventId, (items) => setMissionCount(items.length), () => setMissionCount(0)),
    [eventId]
  );

  const openSheet = (type: "missions" | "leaderboard" | "timeline") => {
    tapHaptic();
    setSheet(type);
  };

  // The plan pill's label is the one place a guest sees where they are in a multi-day event without
  // opening anything: "Day 3 · Fushimi Inari". Falls back to the bare stage label on an undated
  // event and to "The plan" when nothing is active — never to an empty pill.
  const stages = eventInfo?.stages ?? [];
  const activeStage = stages.find((s) => s.stageId === (eventInfo?.activeStage ?? null));
  const activeDay = dayLabelFromIndex(activeStage?.day);
  const planLabel = activeStage
    ? activeDay
      ? `${activeDay} · ${activeStage.label}`
      : activeStage.label
    : "The plan";

  return (
    <section className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between gap-2 px-4 pb-2">
        <div className="flex items-center gap-2 min-w-0">
          {/* Hidden entirely when the host published no timeline: a button that opens an empty sheet
           * is worse than no button. */}
          {stages.length > 0 && (
            <button
              type="button"
              onClick={() => openSheet("timeline")}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full glass-card hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-all active:scale-95 cursor-pointer shadow-md font-medium min-w-0"
              title="The event timeline"
            >
              <CalendarDays className="w-3.5 h-3.5 text-[var(--accent)] stroke-[2] shrink-0" />
              <span className="truncate">{planLabel}</span>
            </button>
          )}

          {missionCount > 0 && (
            <button
              type="button"
              onClick={() => openSheet("missions")}
              className="flex items-center gap-1.5 text-xs px-3.5 py-1.5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] border border-[var(--accent)]/30 hover:bg-[var(--accent)]/25 transition-all active:scale-95 font-medium cursor-pointer shadow-md font-mono tabular-nums shrink-0"
            >
              <Target className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>{missionCount}</span>
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={() => openSheet("leaderboard")}
          aria-label="Leaderboard"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full glass-card hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-all active:scale-95 cursor-pointer shadow-md font-medium shrink-0"
          title="Leaderboard"
        >
          <Trophy className="w-3.5 h-3.5 text-amber-400 stroke-[2]" />
          <span>Leaderboard</span>
        </button>
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
      {sheet === "timeline" && (
        <TimelineSheet eventInfo={eventInfo} onClose={() => setSheet("none")} />
      )}
    </section>
  );
}
