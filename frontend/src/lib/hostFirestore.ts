// The host console's one live listener — kept apart from lib/firestore.ts (a separate, host-only
// surface; the event document is unreadable by any client that isn't this event's host, per
// firestore.rules: `match /events/{eventId} { allow read: if isHost(eventId) || isAdmin(); }`).

import { doc, onSnapshot, Timestamp, type Unsubscribe } from "firebase/firestore";
import { db } from "./firebase";
import type { DirectorTickState, HostEventDoc } from "./hostTypes";

export function listenHostEvent(
  eventId: string,
  onData: (event: HostEventDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId),
    (snap) => onData(snap.exists() ? ({ eventId: snap.id, ...snap.data() } as HostEventDoc) : null),
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
