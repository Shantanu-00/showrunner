"use client";

// The door, client side: anonymous sign-in → `POST /join` → force-refresh the ID token.
//
// Every guest surface used to start with `ensureAnonymousAuth()` alone, because "signed in" *was*
// membership — `firestore.rules`'s `isMember()` took no eventId. It does now, and membership rides in
// a `members` custom claim, so a surface that subscribes to Firestore before this resolves gets
// permission-denied on every listener. `ensureMembership(eventId)` is therefore the new first call on
// every guest surface, and it must resolve before anything renders a query.
//
// Three properties matter and all three are here rather than at four call sites:
//
//  - **Idempotent and de-duplicated.** React 18 mounts effects twice in development, and a shell can
//    re-run its bootstrap on an eventId change. One in-flight promise per eventId, cached, so a double
//    mount is one HTTP request. The server is idempotent too (a rejoin takes no seat), but doing it
//    twice would still cost a token refresh, and a token refresh invalidates every open listener.
//  - **Skipped entirely when the claim is already there.** A returning guest's ID token already
//    carries `members: [eventId]`; there is no reason to spend a round trip re-asserting it. Hosts are
//    covered by the same check because `isMember(eventId)` ORs `isHost(eventId)` in.
//  - **The code rides in and then out of the URL.** An invite link is `/join/{eventId}?joinCode=…`,
//    and a code left in the address bar is a code in browser history, in a screenshot, and in whatever
//    the guest forwards to a friend. It is stripped with `replaceState` — no navigation, so nothing
//    re-mounts — exactly as `HostConsoleShell` already does with `?hostCode=`.

import { joinEvent } from "./api";
import { ApiError } from "./api";
import { ensureAnonymousAuth, hasMembership, refreshClaims } from "./firebase";

export type MembershipState =
  | { status: "member" }
  /** Invite-only, and no code was presented (or the one presented was wrong/expired). The join screen
   * asks for one; nothing else on the surface can render until it is given. */
  | { status: "needs-code"; message: string }
  /** The seat cap is full, or the event has wrapped, or the device is banned. Distinct from
   * `needs-code` because no code the guest could type would fix it — the host has to act. */
  | { status: "refused"; message: string }
  /** Network/cold-start failure. Worth distinguishing so a surface can offer "try again" rather than
   * telling a guest at a wedding that they are not invited. */
  | { status: "error"; message: string };

const inFlight = new Map<string, Promise<MembershipState>>();

/** Pull `?joinCode=` out of the URL and erase it, without a navigation. */
function takeJoinCodeFromUrl(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("joinCode");
  if (!code) return undefined;
  params.delete("joinCode");
  const query = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
  );
  return code;
}

async function join(eventId: string, code: string | undefined): Promise<MembershipState> {
  await ensureAnonymousAuth();
  if (!code && (await hasMembership(eventId))) return { status: "member" };
  try {
    await joinEvent(eventId, code);
  } catch (err) {
    const api = err instanceof ApiError ? err : null;
    if (api?.code === "CODE_REQUIRED" || api?.code === "BAD_CODE") {
      return { status: "needs-code", message: api.message };
    }
    if (api?.code === "EVENT_FULL" || api?.code === "EVENT_CLOSED" || api?.code === "GUEST_BANNED") {
      return { status: "refused", message: api.message };
    }
    if (api && api.status >= 400 && api.status < 500) {
      return { status: "refused", message: api.message };
    }
    return {
      status: "error",
      message: api?.message ?? "Couldn't reach the event just now — check your connection.",
    };
  }
  // The claim exists on the server; the token in memory predates it. Without this force-refresh every
  // listener this page is about to open would be evaluated against a token that has no `members`.
  await refreshClaims();
  return { status: "member" };
}

/**
 * Sign in anonymously, join `eventId`, and refresh the ID token so the `members` claim is live.
 *
 * Call this instead of `ensureAnonymousAuth()` on any surface that reads Firestore. Resolves to
 * `{status: "member"}` on the happy path — including the common case where the claim was already
 * there and no request was made.
 *
 * `code` may be passed explicitly (a join screen's input); otherwise `?joinCode=` is read from the URL
 * and stripped. Presenting a code always forces the round trip: it is how a guest recovers from a
 * membership claim that a host has since revoked by rotating the code.
 */
export function ensureMembership(eventId: string, code?: string): Promise<MembershipState> {
  const presented = code ?? takeJoinCodeFromUrl();
  const key = presented ? `${eventId}#${presented}` : eventId;
  const existing = inFlight.get(key);
  if (existing) return existing;
  const promise = join(eventId, presented).then((state) => {
    // A failure must not be cached: a guest who types the right code after a wrong one, or retries
    // after a cold start, has to be able to get in. Success is cached because the claim is durable.
    if (state.status !== "member") inFlight.delete(key);
    return state;
  });
  inFlight.set(key, promise);
  return promise;
}
