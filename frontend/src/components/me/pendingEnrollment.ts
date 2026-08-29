"use client";

// "You have already asked; the host has not answered yet."
//
// Under the host-approved identity model (spec 02 §3) a held enrollment grants *nothing* — no
// `personId` custom claim, no face links. That is the correct security property and it creates a UX
// problem the claim can no longer solve: on the next page load there is nothing in the ID token to
// distinguish "never enrolled" from "enrolled, waiting", so `/me` would offer the selfie ritual again
// to somebody who just completed it. They would enrol a second time, and the host would get two
// review cards for one person.
//
// The server cannot answer this cheaply either: `GET …/claims?status=held` is host-authed by design
// (it carries other guests' selfies), and a guest-facing "do I have a pending claim" endpoint would be
// a new surface for one boolean. So the marker is local, which is the right scope anyway — it belongs
// to this browser, exactly like the enrollment it records.
//
// It is a hint, never a gate. Losing it (cleared storage, a second device) costs one redundant
// enrollment, which the claim rate limit bounds and the host can deny in one tap. The authority is
// still the `personId` claim: the moment that appears, approval has happened and the marker is dropped.

const KEY = "showrunner.pendingEnrollment";

type Store = Record<string, { personId: string | null; at: string }>;

function read(): Store {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Store) : {};
  } catch {
    return {};
  }
}

function write(store: Store): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    // Private-browsing quota failures are not worth surfacing: the cost is one redundant selfie.
  }
}

export function markPendingEnrollment(eventId: string, personId: string | null): void {
  const store = read();
  store[eventId] = { personId, at: new Date().toISOString() };
  write(store);
}

export function pendingEnrollment(eventId: string): { personId: string | null } | null {
  const entry = read()[eventId];
  return entry ? { personId: entry.personId } : null;
}

export function clearPendingEnrollment(eventId: string): void {
  const store = read();
  if (!(eventId in store)) return;
  delete store[eventId];
  write(store);
}
