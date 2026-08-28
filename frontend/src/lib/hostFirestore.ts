// The host console's one live listener — kept apart from lib/firestore.ts (a separate, host-only
// surface; the event document is unreadable by any client that isn't this event's host, per
// firestore.rules: `match /events/{eventId} { allow read: if isHost(eventId) || isAdmin(); }`).

import { doc, onSnapshot, type Unsubscribe } from "firebase/firestore";
import { db } from "./firebase";
import type { HostEventDoc } from "./hostTypes";

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
