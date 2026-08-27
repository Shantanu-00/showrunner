import { getUid, auth } from "./firebase";
import type { UploadBatchRequest, UploadBatchResponse } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

async function authedFetch(path: string, init: RequestInit): Promise<Response> {
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
