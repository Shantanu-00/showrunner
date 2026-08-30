"use client";

import { useEffect, useState } from "react";
import { Users, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { PersonDoc, Tier } from "@/lib/types";
import { listenPeople } from "@/lib/firestore";
import { setPersonTier } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import { PersonEnrollForm } from "./PersonEnrollForm";

const TIER_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "0 · Principal" },
  { value: 1, label: "1 · Inner circle" },
  { value: 2, label: "2 · Named VIP" },
  { value: 3, label: "3 · Guest" },
];

/**
 * The event's people roster (spec 13 §7) — who Showrunner is tracking coverage for, their tier,
 * and whether the real person has actually claimed their album. Mounted in `HostConsoleShell`
 * alongside `ClaimReviewPanel`, which is the *other* half of this trust boundary: adding someone
 * here never opens an album by itself, and approving a claim there is the only thing that does.
 */
export function PeoplePanel({ eventId }: { eventId: string }) {
  const [people, setPeople] = useState<PersonDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(
    () => listenPeople(eventId, setPeople, () => setError("Couldn't load the people list.")),
    [eventId]
  );

  const count = people?.length ?? 0;

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-center gap-2 mb-2">
        <Users className="w-4 h-4 text-[var(--accent)]" />
        <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
          People
        </h3>
        {people !== null && count > 0 && (
          <span className="text-[11px] font-mono font-bold tabular-nums px-2 py-0.5 rounded-full bg-white/10 text-[var(--ivory)]">
            {count}
          </span>
        )}
      </div>
      <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
        Everyone Showrunner is tracking for this event — how featured they are, and whether they&rsquo;ve
        claimed their own album yet.
      </p>

      {error && (
        <p className="text-xs text-[var(--danger)] mb-4 p-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
          {error}
        </p>
      )}

      {people === null && !error && (
        <div className="space-y-2 mb-6">
          {[0, 1].map((i) => (
            <div key={i} className="h-16 rounded-xl skeleton-shimmer bg-white/5" />
          ))}
        </div>
      )}

      {people !== null && count === 0 && (
        <p className="text-xs text-[var(--ink-muted)] mb-6 p-5 rounded-2xl bg-white/[0.03] border border-white/5 leading-relaxed">
          Nobody added yet. Add your group below, or let people self-enroll from the join link —
          both land here.
        </p>
      )}

      {people !== null && count > 0 && (
        <div className="space-y-2 mb-6">
          {people.map((person) => (
            <PersonRow key={person.personId} eventId={eventId} person={person} />
          ))}
        </div>
      )}

      <PersonEnrollForm eventId={eventId} />
    </section>
  );
}

function PersonRow({ eventId, person }: { eventId: string; person: PersonDoc }) {
  const [tier, setTier] = useState(person.tier);
  const [saving, setSaving] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  // The listener is the source of truth; a tier changed elsewhere (or rolled back by another
  // host's browser) still has to win over whatever this row last optimistically set.
  useEffect(() => setTier(person.tier), [person.tier]);

  async function changeTier(next: Tier) {
    const prev = tier;
    setTier(next);
    setSaving(true);
    setRowError(null);
    try {
      await setPersonTier(eventId, person.personId, next);
    } catch (err) {
      setTier(prev);
      setRowError(err instanceof ApiError ? err.message : "Couldn't update tier — try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/10">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-[var(--ivory)] truncate">
              {person.displayName || "Unnamed person"}
            </span>
            {person.hostEnrolled && (
              <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-[var(--ink-faint)]">
                Host-added
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-1 text-[11px]">
            {person.claimApproved ? (
              <>
                <ShieldCheck className="w-3 h-3 text-[var(--ok)]" />
                <span className="text-[var(--ok)]">Album active</span>
              </>
            ) : (
              <>
                <ShieldQuestion className="w-3 h-3 text-[var(--ink-faint)]" />
                <span className="text-[var(--ink-faint)]">Not claimed yet</span>
              </>
            )}
          </div>
        </div>

        <select
          value={tier}
          disabled={saving}
          onChange={(e) => void changeTier(Number(e.target.value) as Tier)}
          aria-label={`Tier for ${person.displayName || "this person"}`}
          className="shrink-0 px-3 py-2 rounded-lg bg-black/40 border border-white/10 text-xs text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
        >
          {TIER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {rowError && <p className="text-[11px] text-[var(--danger)] mt-2">{rowError}</p>}
    </div>
  );
}
