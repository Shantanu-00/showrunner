import { getUid, auth } from "./firebase";
import { RING_VALUE } from "./types";
import type {
  ClaimLinkResponse,
  ConsentRing,
  EnrollResponse,
  EventPublicInfo,
  RedeemResponse,
  UploadBatchRequest,
  UploadBatchResponse,
  VisibilityResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

/** Every derived bucket has `--public-access-prevention`, so `MediaDoc.thumbUri`/`displayUri`
 * (raw `gs://` values, kept that way because backend consumers read the bytes directly) can never
 * be an `<img src>`. This path is the one that can — `api/media.py` re-checks visibility on every
 * request and 302s to a short-lived signed URL, so a subject veto revokes the bytes immediately
 * instead of leaving a public object fetchable by anyone who already has the link. */
export function mediaRenderPath(eventId: string, mediaId: string, variant: "thumb" | "display"): string {
  return `/v1/events/${eventId}/media/${mediaId}/render?variant=${variant}`;
}

/** For the public-only surfaces (kiosk, public gallery): the render endpoint's public branch is
 * deliberately unauthenticated (same reasoning as the reel video), so these can go straight into
 * `<img src>` with no token. Pool/self-tier surfaces need `useAuthedImage` instead. */
export function mediaRenderUrl(eventId: string, mediaId: string, variant: "thumb" | "display"): string {
  return `${API_URL}${mediaRenderPath(eventId, mediaId, variant)}`;
}

/** Exported for `lib/hostApi.ts` — the host console is a separate surface (spec 12 §8) with its
 * own file, but there is exactly one way any client here talks to `api`, and it shouldn't be
 * reinvented per surface. */
export async function authedFetch(path: string, init: RequestInit): Promise<Response> {
  const token = await auth?.currentUser?.getIdToken();
  return fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
}

/** Small, honest error the UI shows inline — never a stack trace, never a crash. Every backend
 * error body is `{code, message, ...extra}` (`shared/errors.py`); `code`/`contactUrl` ride along
 * so a surface that cares (the host console's `CAPACITY` message) can read them, while every
 * existing caller that only checks `.status` is unaffected. */
export class ApiError extends Error {
  code?: string;
  contactUrl?: string;

  constructor(public status: number, message: string, code?: string, contactUrl?: string) {
    super(message);
    this.code = code;
    this.contactUrl = contactUrl;
  }
}

export async function authedJson<T>(path: string, init: RequestInit): Promise<T> {
  const res = await authedFetch(path, init);
  if (!res.ok) {
    const fallback = `${init.method ?? "GET"} ${path} → ${res.status}`;
    let detail: { code?: string; message?: string; contactUrl?: string } = {};
    try {
      const body = await res.json();
      detail = (body?.detail ?? body) as typeof detail;
    } catch {
      // A non-JSON error body (a proxy 502, a cold start) — the fallback message covers it.
    }
    throw new ApiError(res.status, detail.message || fallback, detail.code, detail.contactUrl);
  }
  return res.json();
}

/** POST /v1/events/{eventId}/uploads — spec 01 §3. */
export async function registerUploadBatch(
  eventId: string,
  body: UploadBatchRequest
): Promise<UploadBatchResponse> {
  if (!getUid()) throw new Error("not authenticated");
  const res = await authedFetch(`/v1/events/${eventId}/uploads`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`upload registration failed: ${res.status}`);
  }
  return res.json();
}

/** POST /v1/events/{eventId}/uploads/{mediaId}/refresh-url — spec 01 §3. */
export async function refreshUploadUrl(
  eventId: string,
  mediaId: string
): Promise<{ signedUrl?: string; resumableSessionUri?: string; expiresAt: string }> {
  const res = await authedFetch(`/v1/events/${eventId}/uploads/${mediaId}/refresh-url`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    throw new Error(`url refresh failed: ${res.status}`);
  }
  return res.json();
}

/** GET /v1/events/{eventId}/public — the narrow, non-sensitive bootstrap (name/theme/stage). */
export async function getEventPublic(eventId: string): Promise<EventPublicInfo> {
  return authedJson<EventPublicInfo>(`/v1/events/${eventId}/public`, { method: "GET" });
}

/** POST /v1/events/{eventId}/people — selfie enrollment, consent moment C2 (spec 02 §3/§7).
 * Identity is granted server-side directly on the caller's own uid — the response carries no
 * usable token; call `refreshClaims()` after a `linked`/`held_for_review` outcome. */
export async function enrollPerson(
  eventId: string,
  body: { selfie: string; displayName?: string; biometricConsent: true; retentionNoticeShown?: boolean }
): Promise<EnrollResponse> {
  return authedJson(`/v1/events/${eventId}/people`, { method: "POST", body: JSON.stringify(body) });
}

/** POST /v1/events/{eventId}/claim-links — "save your album link" (spec 02 §3.1). URL shape is
 * `{origin}/events/{eventId}/claim#{code}` — the code rides the fragment, never a query param. */
export async function createClaimLink(eventId: string): Promise<ClaimLinkResponse> {
  return authedJson(`/v1/events/${eventId}/claim-links`, { method: "POST", body: "{}" });
}

/** POST /v1/claim — magic-link redemption. Grants `personId` directly to whichever uid is on
 * the caller's own bearer token (anonymous auth must already have run); no token to sign in
 * with — the caller just force-refreshes its ID token afterward. */
export async function claimByCode(code: string): Promise<RedeemResponse> {
  return authedJson(`/v1/claim`, { method: "POST", body: JSON.stringify({ code }) });
}

/** POST /v1/events/{eventId}/people/reclaim — selfie re-claim on a fresh device (spec 02 §3.2). */
export async function reclaimBySelfie(eventId: string, selfie: string): Promise<EnrollResponse> {
  return authedJson(`/v1/events/${eventId}/people/reclaim`, {
    method: "POST",
    body: JSON.stringify({ selfie }),
  });
}

/** POST /v1/events/{eventId}/media/{id}/consent — the padlock override, C3 (spec 02 §4/§7). */
export async function setMediaConsent(
  eventId: string,
  mediaId: string,
  ring: ConsentRing
): Promise<VisibilityResponse> {
  return authedJson(`/v1/events/${eventId}/media/${mediaId}/consent`, {
    method: "POST",
    body: JSON.stringify({ ring: RING_VALUE[ring] }),
  });
}

/** POST /v1/events/{eventId}/media/{id}/subject-veto — "hide me from public", C4 (spec 02 §4/§7).
 * `hide: false` reverses it (the lightbox toggles both directions). */
export async function setSubjectVeto(
  eventId: string,
  mediaId: string,
  hide: boolean
): Promise<VisibilityResponse> {
  return authedJson(`/v1/events/${eventId}/media/${mediaId}/subject-veto`, {
    method: "POST",
    body: JSON.stringify({ hide }),
  });
}

/** DELETE /v1/events/{eventId}/people/me — full deletion flow (spec 02 §5/§7). */
export async function deleteMyData(eventId: string): Promise<void> {
  await authedJson(`/v1/events/${eventId}/people/me`, { method: "DELETE" });
}
