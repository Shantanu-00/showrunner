// The host console's one live listener — kept apart from lib/firestore.ts (a separate, host-only
// surface; the event document is unreadable by any client that isn't this event's host, per
// firestore.rules: `match /events/{eventId} { allow read: if isHost(eventId) || isAdmin(); }`).

import { doc, onSnapshot, Timestamp, type Unsubscribe } from "firebase/firestore";
import { db } from "./firebase";
import type { DirectorTickState, EventStageDoc, HostEventDoc } from "./hostTypes";

/** Every `dt.datetime` field the backend writes (`schemas/event.py`) round-trips through the
 * Firestore JS SDK as a native `Timestamp`, never the ISO string `HostEventDoc` declares — the
 * REST API is what serializes those to strings, and this listener bypasses it entirely. Same fact
 * `lastTickAt` below already accounts for; stages and the access door just as easily carry one. */
function isoOrNull(v: unknown): string | null {
  if (v instanceof Timestamp) return v.toDate().toISOString();
  return typeof v === "string" ? v : null;
}

function normalizeStage(s: Record<string, unknown>): EventStageDoc {
  return { ...(s as unknown as EventStageDoc), startsAt: isoOrNull(s.startsAt), endsAt: isoOrNull(s.endsAt) };
}

export function listenHostEvent(
  eventId: string,
  onData: (event: HostEventDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId),
    (snap) => {
      if (!snap.exists()) {
        onData(null);
        return;
      }
      const data = snap.data() as Record<string, unknown>;
      const stages = Array.isArray(data.stages)
        ? data.stages.map((s) => normalizeStage(s as Record<string, unknown>))
        : [];
      const access = data.access as Record<string, unknown> | undefined;
      onData({
        ...(data as unknown as HostEventDoc),
        eventId: snap.id,
        stages,
        ...(access ? { access: { ...access, codeRotatedAt: isoOrNull(access.codeRotatedAt) } } : {}),
      });
    },
    onError
  );
}

/** `ledger/directorState` — same rule (`isHost(eventId)`) as the event document above, read
 * directly rather than through `GET /console` so the next-tick countdown is a live listener,
 * never a poll (the stack rule every other guest/host surface already follows). */
export function listenDirectorState(
  eventId: string,
  onData: (state: DirectorTickState) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "ledger", "directorState"),
    (snap) => {
      const data = snap.data();
      const lastTickAt = data?.lastTickAt;
      onData({
        lastTickAtMs: lastTickAt instanceof Timestamp ? lastTickAt.toMillis() : null,
        tickCount: Number(data?.tickCount ?? 0),
      });
    },
    onError
  );
}
