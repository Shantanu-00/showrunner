"use client";

/** The browser's memory of events it created.
 *
 * A host creates an event anonymously — there is no account to hang a "my events" list off, and the
 * recovery code is shown exactly once. Without this, closing the tab loses the event id, and the
 * only way back in is the code (which recovers access) *plus* the id (which the code itself is
 * supposed to tell you). This is the local half of that: the id and the name, so `/host` can offer
 * "continue to your event" without a round trip and without an account.
 *
 * It is a convenience, never an authority — the `host` custom claim on the uid is what actually
 * grants access, and the console re-checks it on every load. A cleared browser loses this list and
 * the recovery code still works.
 *
 * Stored as a list from the outset because one browser legitimately owns several events (a host
 * running a rehearsal event and the wedding itself), which is also the direction the `host` claim
 * itself is moving.
 */

const KEY = "showrunner.hostEvents.v1";
const LIMIT = 20;

export interface RememberedEvent {
  eventId: string;
  name: string;
  /** Epoch ms, for most-recent-first ordering. */
  at: number;
}

function isRemembered(value: unknown): value is RememberedEvent {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.eventId === "string" && v.eventId.length > 0 && typeof v.name === "string";
}

export function listRememberedEvents(): RememberedEvent[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isRemembered)
      .map((e) => ({ ...e, at: typeof e.at === "number" ? e.at : 0 }))
      .sort((a, b) => b.at - a.at);
  } catch {
    // Private-mode Safari throws on localStorage, and a hand-corrupted value shouldn't break /host.
    return [];
  }
}

/** Idempotent: re-remembering the same event updates its name and moves it to the top. */
export function rememberEvent(eventId: string, name: string): void {
  if (typeof window === "undefined" || !eventId) return;
  try {
    const next = [
      { eventId, name: name || eventId, at: Date.now() },
      ...listRememberedEvents().filter((e) => e.eventId !== eventId),
    ].slice(0, LIMIT);
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Losing the convenience is acceptable; failing the creation flow over it is not.
  }
}

export function forgetEvent(eventId: string): void {
  if (typeof window === "undefined") return;
  try {
    const next = listRememberedEvents().filter((e) => e.eventId !== eventId);
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // See above.
  }
}
