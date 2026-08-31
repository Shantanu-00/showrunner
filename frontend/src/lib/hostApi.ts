// Host console API calls — mirrors backend/api/host.py's endpoints one-to-one (spec 08, spec 11).
// Kept apart from lib/api.ts for the same reason as hostTypes.ts: a separate, code-split surface.

import { ApiError, authedJson } from "./api";
import type { GuardianVerdict } from "./types";
import type {
  ConsoleSummary,
  CreateEventResponse,
  EventStageDoc,
  EventTypeProfile,
  HostEnrollResponse,
  HostLinkResponse,
  ItineraryFileMime,
  ItineraryParseOut,
  LifecycleResponse,
  RedeemHostResponse,
  RequiredMoment,
  ReviewQueueResponse,
  SensitivityProfile,
  WrapReport,
} from "./hostTypes";

export { ApiError };

/** `POST /v1/events` (spec 08 §1, spec 13). Itinerary-first creation: every event starts from one
 * neutral profile (pyramid topology, context-dependent dials, empty glossary), which the host can
 * edit directly from the Settings panel (`updateProfile` below). No template picker. */
export async function createEvent(body: {
  name: string;
  timezone: string;
  startDate?: string;
  endDate?: string;
  expectedParticipants?: number;
  accessMode?: "open" | "invite";
}): Promise<CreateEventResponse> {
  return authedJson("/v1/events", { method: "POST", body: JSON.stringify(body) });
}

export async function redeemHostCode(code: string): Promise<RedeemHostResponse> {
  return authedJson("/v1/host-claim", { method: "POST", body: JSON.stringify({ code }) });
}

export async function createHostLink(eventId: string): Promise<HostLinkResponse> {
  return authedJson(`/v1/events/${eventId}/host-links`, { method: "POST", body: "{}" });
}

export async function updateProfile(
  eventId: string,
  body: {
    vipTopology?: "pyramid" | "flat";
    sensitivityProfile?: SensitivityProfile;
    culturalGlossary?: string[];
    requiredMomentsTemplate?: RequiredMoment[];
  }
): Promise<{ eventTypeProfile: EventTypeProfile }> {
  return authedJson(`/v1/events/${eventId}/profile`, { method: "POST", body: JSON.stringify(body) });
}

/** `POST /v1/itinerary/extract` — Standalone Gemini 3.7 Flash AI extraction on Step 1.
 * Auto-suggests event name, dates, timezone, headcount, access mode, people, and stages. */
export async function extractItinerary(body: {
  rawText?: string;
  fileBase64?: string;
  fileMime?: ItineraryFileMime;
}): Promise<ItineraryParseOut> {
  return authedJson("/v1/itinerary/extract", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `POST /v1/events/{eventId}/itinerary/parse` (spec 08 §3.2, spec 13's PDF/screenshot extension).
 * At least one of `rawText`/`fileBase64` is required — the server 400s `EMPTY` otherwise. */
export async function parseItinerary(
  eventId: string,
  body: { rawText?: string; fileBase64?: string; fileMime?: ItineraryFileMime }
): Promise<ItineraryParseOut> {
  return authedJson(`/v1/events/${eventId}/itinerary/parse`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function saveStages(
  eventId: string,
  stages: EventStageDoc[]
): Promise<{ stages: EventStageDoc[] }> {
  return authedJson(`/v1/events/${eventId}/stages`, { method: "PUT", body: JSON.stringify({ stages }) });
}

export async function goLive(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/go-live`, { method: "POST", body: "{}" });
}

export async function pauseEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/pause`, { method: "POST", body: "{}" });
}

export async function resumeEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/resume`, { method: "POST", body: "{}" });
}

export async function wrapEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/wrap`, { method: "POST", body: "{}" });
}

export async function finalizeEvent(eventId: string): Promise<WrapReport> {
  return authedJson(`/v1/events/${eventId}/lifecycle/finalize`, { method: "POST", body: "{}" });
}

export async function getWrapReport(eventId: string): Promise<WrapReport> {
  return authedJson(`/v1/events/${eventId}/wrap-report`, { method: "GET" });
}

/** `POST /v1/events/{eventId}/reels` (`backend/api/reels.py::commission_reel`) — the host's
 * commission button. Used by the wrap panel's "Regenerate recap" for the `event_recap` persona;
 * eligibility, the aesthetic/VIP floors and the in-flight/daily caps are all decided server-side
 * by `directors/reel/commission.py`, identically to a director-initiated commission. */
export async function commissionReel(
  eventId: string,
  body: { persona: string; stageId?: string; personId?: string; reason?: string }
): Promise<{ reelId: string; status: string; note?: string }> {
  return authedJson(`/v1/events/${eventId}/reels`, { method: "POST", body: JSON.stringify(body) });
}

/** `GET {API}/v1/events/{eventId}/reels/{reelId}/video` (`backend/api/reels.py::reel_video`) — a
 * relative path, not a fetch: hand it to `useAuthedBlobUrl` (it prefixes the API origin and
 * attaches the host's bearer itself). The host console always fetches this authed rather than
 * branching on the reel's publish state or the event's access mode the way `ReelSlot` does,
 * because a host previewing an unpublished/failed reel needs the bearer regardless, and sending
 * it on an already-public, open-event reel is harmless — the endpoint only inspects the token on
 * the branches that need one. `download=true` only changes the signed URL's
 * content-disposition server-side; the panel doesn't need it because it re-uses the same
 * already-fetched blob for its "Download film" link instead of a second request. */
export function reelVideoPath(eventId: string, reelId: string, download = false): string {
  return `/v1/events/${eventId}/reels/${reelId}/video${download ? "?download=1" : ""}`;
}

export async function setStageOverride(
  eventId: string,
  stageId: string | null
): Promise<{ stageOverride: string | null }> {
  return authedJson(`/v1/events/${eventId}/stage-override`, {
    method: "POST",
    body: JSON.stringify({ stageId }),
  });
}

export async function setFreeze(
  eventId: string,
  frozen: boolean
): Promise<{ publicFrozen: boolean }> {
  return authedJson(`/v1/events/${eventId}/freeze`, {
    method: "POST",
    body: JSON.stringify({ frozen }),
  });
}

export async function getConsoleSummary(eventId: string): Promise<ConsoleSummary> {
  return authedJson(`/v1/events/${eventId}/console`, { method: "GET" });
}

/** `POST /v1/events/{eventId}/people/host-enroll` (spec 13 §7). The host adds a participant from a
 * reference photo — coverage tracking only. **Grants no identity**: no uid link, no custom claim.
 * When the real person later selfie-enrolls, their match against this person is held for the
 * host's own approval (the impersonation guard, spec 02 §3) exactly like any other claim. */
export async function hostEnrollPerson(
  eventId: string,
  body: { photo: string; displayName: string; tier: number; photoConsent: true }
): Promise<HostEnrollResponse> {
  return authedJson(`/v1/events/${eventId}/people/host-enroll`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `POST /v1/events/{eventId}/people/{personId}/tier` (spec 11 §6) — promote/demote one person. */
export async function setPersonTier(
  eventId: string,
  personId: string,
  tier: number
): Promise<{ personId: string; tier: number }> {
  return authedJson(`/v1/events/${eventId}/people/${personId}/tier`, {
    method: "POST",
    body: JSON.stringify({ tier }),
  });
}

/** Photos still waiting on the host. `verdict: "blocked"` lists what the explicit-content gate
 * stopped — those are findable but not releasable (see `decideMedia`). */
export async function getReviewQueue(
  eventId: string,
  verdict: "host_review" | "blocked" = "host_review"
): Promise<ReviewQueueResponse> {
  return authedJson(`/v1/events/${eventId}/media/review-queue?verdict=${verdict}`, {
    method: "GET",
  });
}

/** The host's call on one held photo. Writes `guardian.hostDecision` — never `guardian.verdict`, and
 * never `visibility`, which only `recompute_visibility` may write. `blocked` is not offered: it is the
 * SafeSearch gate's verdict, and the backend rejects it. */
export async function decideMedia(
  eventId: string,
  mediaId: string,
  decision: Exclude<GuardianVerdict, "blocked">,
  note?: string
): Promise<{ mediaId: string; verdict: GuardianVerdict; visibility: string | null }> {
  return authedJson(`/v1/events/${eventId}/media/${mediaId}/review`, {
    method: "POST",
    body: JSON.stringify({ decision, note: note ?? null }),
  });
}
