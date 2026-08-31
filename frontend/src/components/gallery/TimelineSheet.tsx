"use client";

import { CalendarDays, X, Check, Radio } from "lucide-react";
import type { EventPublicInfo } from "@/lib/types";
import { dayLabelFromIndex } from "@/lib/eventTime";

/** The event's plan, for the people at it — a sheet rather than a panel, on purpose.
 *
 * A guest on a five-day trip wants to know what today holds and what they missed, and until now the
 * only timeline anywhere was the host console's editor plus a row of filter chips. But a full
 * itinerary rendered inline above the gallery is exactly the clutter the gallery tab does not need
 * (the photographs are the point), so this reuses the same bottom-sheet pattern
 * `MissionsSheet`/`LeaderboardSheet` already established: one compact pill on the tab, the whole
 * timeline on tap, gone again in one gesture.
 *
 * **Times are member-only and may simply be absent.** `GET …/public` omits `startsAt`/`endsAt` for a
 * caller who has not joined the event yet, and an undated event or an unscheduled stage never had
 * them (spec 13 §1's degradation contract). All three cases render as a label-only row, which is the
 * honest shape: this sheet's job is "here is the plan", and a plan with no clock is still a plan.
 * Nothing here is a listener — the stage list arrives once with the event bootstrap and changes only
 * when the host edits it.
 */
function timeRange(
  startsAt: string | null | undefined,
  endsAt: string | null | undefined,
  timezone: string | undefined
): string | null {
  if (!startsAt) return null;
  const fmt = (iso: string) => {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return null;
    // Formatted in the *event's* timezone, not the phone's: a guest who lands in Japan with a phone
    // still on IST must read the itinerary in the time the event is actually happening in. Same
    // reasoning as `shared/eventtime.py` — the wall clock belongs to the event.
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone || undefined,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(at);
  };
  const from = fmt(startsAt);
  if (!from) return null;
  const to = endsAt ? fmt(endsAt) : null;
  return to ? `${from}–${to}` : from;
}

export function TimelineSheet({
  eventInfo,
  selected,
  onSelect,
  onClose,
}: {
  eventInfo: EventPublicInfo | null;
  /** The stage id (or `day:N`) the gallery is filtered to, or null for everything. */
  selected?: string | null;
  /** Tapping a row filters the gallery to that moment and closes the sheet. This is where the day and
   * phase chips went: the plan is one place, and picking from it is the same gesture as reading it. */
  onSelect?: (stageId: string | null) => void;
  onClose: () => void;
}) {
  const stages = eventInfo?.stages ?? [];
  const activeStageId = eventInfo?.activeStage ?? null;
  const timezone = eventInfo?.timezone;

  // Group by the server-computed day index. Undated events (and any stage without one) collapse into
  // a single unlabelled group, which is exactly how they rendered before multi-day existed.
  const groups = new Map<number | null, typeof stages>();
  for (const s of stages) {
    const key = typeof s.day === "number" && s.day > 0 ? s.day : null;
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const dayKeys = Array.from(groups.keys()).sort((a, b) => {
    if (a === null) return 1;
    if (b === null) return -1;
    return a - b;
  });

  // Everything before the active stage has happened. Array order *is* time order — `PUT /stages`
  // sorts by `startsAt` on write (spec 13 §1), so no client anywhere re-sorts it.
  const activeIndex = stages.findIndex((s) => s.stageId === activeStageId);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-3xl p-6 pb-8 max-h-[80vh] overflow-y-auto scroll-slim glass-card border-t border-[var(--hairline-accent)] shadow-2xl"
        style={{ background: "rgba(23, 16, 20, 0.96)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4 border-b border-white/10 mb-5">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
              <CalendarDays className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
                The plan
              </h3>
              <p className="text-[11px] text-[var(--ink-muted)]">
                {eventInfo?.dayCount
                  ? `${eventInfo.dayCount} days · ${stages.length} moments`
                  : `${stages.length} moments`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-2 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {stages.length === 0 ? (
          <div className="text-center py-10 text-[var(--ink-muted)]">
            <CalendarDays className="w-10 h-10 mx-auto mb-2 opacity-30 text-[var(--gold-500)]" />
            <p className="text-sm">No timeline yet.</p>
            <p className="text-xs mt-1">The host hasn&rsquo;t published a plan for this event.</p>
          </div>
        ) : (
          <div className="space-y-5">
            {selected && (
              <button
                type="button"
                onClick={() => onSelect?.(null)}
                className="w-full py-2.5 rounded-xl bg-white/[0.06] border border-white/10 text-xs font-semibold text-[var(--ivory)] hover:border-[var(--accent)]/50 transition-colors"
              >
                Show every moment
              </button>
            )}
            {dayKeys.map((day) => (
              <div key={day ?? "undated"}>
                {day !== null && (
                  <button
                    type="button"
                    onClick={() => onSelect?.(selected === `day:${day}` ? null : `day:${day}`)}
                    className={`text-[11px] uppercase mb-2 font-semibold transition-colors ${
                      selected === `day:${day}` ? "underline" : "hover:underline"
                    }`}
                    style={{ color: "var(--accent)", letterSpacing: "0.1em" }}
                  >
                    {dayLabelFromIndex(day)}
                    {eventInfo?.dayCount ? ` of ${eventInfo.dayCount}` : ""}
                    <span className="sr-only"> — show only this day&rsquo;s photos</span>
                  </button>
                )}
                <ol className="space-y-1.5">
                  {(groups.get(day) ?? []).map((s) => {
                    const isActive = s.stageId === activeStageId;
                    const position = stages.findIndex((x) => x.stageId === s.stageId);
                    const isPast = activeIndex >= 0 && position < activeIndex;
                    const range = timeRange(s.startsAt, s.endsAt, timezone);
                    return (
                      <li key={s.stageId}>
                       <button
                        type="button"
                        onClick={() => onSelect?.(selected === s.stageId ? null : s.stageId)}
                        className={`w-full text-left flex items-center gap-3 rounded-xl px-3 py-2.5 border transition-colors active:scale-[0.99] ${
                          selected === s.stageId
                            ? "bg-[var(--accent)]/20 border-[var(--accent)]"
                            : isActive
                              ? "bg-[var(--accent)]/12 border-[var(--accent)]/40"
                              : "bg-white/[0.03] border-white/[0.06] hover:border-white/20"
                        }`}
                      >
                        <span
                          className={`shrink-0 w-5 h-5 rounded-full flex items-center justify-center ${
                            isActive
                              ? "bg-[var(--accent)] text-slate-950"
                              : isPast
                                ? "bg-white/10 text-[var(--ink-muted)]"
                                : "border border-white/15"
                          }`}
                        >
                          {isActive ? (
                            <Radio className="w-3 h-3" />
                          ) : isPast ? (
                            <Check className="w-3 h-3 stroke-[3]" />
                          ) : null}
                        </span>
                        <span
                          className={`flex-1 text-sm truncate ${
                            isActive
                              ? "text-[var(--ivory)] font-semibold"
                              : isPast
                                ? "text-[var(--ink-muted)]"
                                : "text-[var(--ivory)]"
                          }`}
                        >
                          {s.label}
                        </span>
                        {/* No time is a normal state, not a gap to apologise for — see the header
                         * comment. The "Now" chip stands in so the row is never bare. */}
                        {range ? (
                          <span className="shrink-0 text-[11px] font-mono tabular-nums text-[var(--ink-muted)]">
                            {range}
                          </span>
                        ) : isActive ? (
                          <span className="shrink-0 text-[11px] font-semibold text-[var(--accent)]">
                            Now
                          </span>
                        ) : null}
                       </button>
                      </li>
                    );
                  })}
                </ol>
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-6 py-3 rounded-full btn-secondary text-sm font-medium"
        >
          Close
        </button>
      </div>
    </div>
  );
}
