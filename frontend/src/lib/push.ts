"use client";

// Web Push opt-in for the guest PWA — the client half of `backend/shared/push.py`.
//
// The problem this closes: `BountyBanner` is a Firestore listener on a live tab, so until now the
// director's asks reached only guests who happened to be staring at their phones. Everyone else —
// which at a dinner is everyone — was told nothing, and `BOUNTY_ASSIGN_TIMEOUT_MINUTES` expired
// unanswered because the assignee never saw the assignment.
//
// Four things here are worth reading before changing them.
//
// **1. Permission is only ever requested from a user gesture.** `Notification.requestPermission()`
// called on mount is the single fastest way to get permanently blocked: Chrome and Safari both treat
// an unprompted permission dialog as abuse, and `denied` is not recoverable from JavaScript — the
// guest has to go into browser settings, which they will not. So `enablePush()` is only ever called
// from an onClick, and `PushOptIn` explains what it is for *before* the browser asks.
//
// **2. Everything is capability-detected, nothing is UA-sniffed.** `pushSupport()` reports why push
// is unavailable so the UI can say something true and specific. The interesting case is iOS: Safari
// has supported Web Push since 16.4 but **only for a PWA that has been added to the home screen** —
// in a normal Safari tab `window.Notification` is simply absent. That is a platform rule, not
// something a build can work around, so the honest response is to detect it and show the guest the
// two-tap install path rather than a button that cannot work.
//
// **3. The token is re-registered on every app open.** FCM registration tokens rotate; a client that
// registered once on day one would go quietly unreachable partway through a five-day trip, with no
// error anywhere. `syncPush()` is cheap (one `getToken` against an existing SW registration, one
// POST) and idempotent server-side, so it just runs whenever permission is already granted.
//
// **4. The service worker is ours, and it is passed explicitly.** `getToken` would otherwise register
// `/firebase-messaging-sw.js` on its own; we register it first so the app controls scope and update
// timing, and so `public/firebase-messaging-sw.js` (which imports no Firebase SDK — see its header)
// is unambiguously the worker in play.

import { getMessaging, getToken, deleteToken, onMessage, type Messaging } from "firebase/messaging";
import { authedFetch } from "./api";
import { auth } from "./firebase";

const VAPID_KEY = process.env.NEXT_PUBLIC_FIREBASE_VAPID_KEY;
const SW_PATH = "/firebase-messaging-sw.js";

/** Why push is or is not available here. `reason` is what the UI renders — each value has different
 * copy and a different call to action, which is the whole point of not collapsing this to a boolean. */
export type PushSupport =
  | { ok: true }
  /** iOS/iPadOS Safari in a browser tab. Web Push exists on 16.4+ but only for an installed PWA. */
  | { ok: false; reason: "needs-install" }
  /** Permission was refused. Not recoverable from JS — only from browser settings. */
  | { ok: false; reason: "blocked" }
  /** No Push API at all (a desktop browser with notifications disabled, an old WebView). */
  | { ok: false; reason: "unsupported" }
  /** Built without `NEXT_PUBLIC_FIREBASE_VAPID_KEY` — a deploy problem, not a device one. */
  | { ok: false; reason: "not-configured" };

/** Whether this page is running as an installed PWA rather than a browser tab. Both checks are
 * needed: `display-mode: standalone` is the standard and works on Android/desktop, while iOS reports
 * it through the non-standard `navigator.standalone` that predates the media query. */
export function isInstalledPwa(): boolean {
  if (typeof window === "undefined") return false;
  const standalone = window.matchMedia?.("(display-mode: standalone)")?.matches;
  const iosStandalone = (window.navigator as { standalone?: boolean }).standalone;
  return Boolean(standalone || iosStandalone);
}

/** Best-effort "is this an iPhone/iPad". Used **only** to choose between two honest explanations of
 * an already-detected failure ("add to home screen" vs "your browser can't"), never to gate a
 * capability — the capability itself is always feature-detected above. */
function isIos(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    // iPadOS 13+ reports itself as a Mac; the touch-point count is the standard way to tell them apart.
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

export function pushSupport(): PushSupport {
  if (typeof window === "undefined") return { ok: false, reason: "unsupported" };
  if (!VAPID_KEY) return { ok: false, reason: "not-configured" };
  const hasApi =
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (!hasApi) {
    // On iOS the Push API is present *only* inside an installed PWA, so its absence there is an
    // instruction ("install me"), whereas anywhere else it is a dead end.
    return { ok: false, reason: isIos() && !isInstalledPwa() ? "needs-install" : "unsupported" };
  }
  if (Notification.permission === "denied") return { ok: false, reason: "blocked" };
  return { ok: true };
}

export function pushPermission(): NotificationPermission | "unavailable" {
  if (typeof window === "undefined" || !("Notification" in window)) return "unavailable";
  return Notification.permission;
}

let messagingSingleton: Messaging | null = null;

function messaging(): Messaging {
  if (!messagingSingleton) messagingSingleton = getMessaging();
  return messagingSingleton;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const existing = await navigator.serviceWorker.getRegistration(SW_PATH);
  if (existing) return existing;
  // `scope: "/"` so one worker covers `/join`, `/kiosk` and the claim routes. A worker registered at
  // its own path's scope would only control `/` and could not be the controller for a deep link.
  return navigator.serviceWorker.register(SW_PATH, { scope: "/" });
}

async function currentToken(): Promise<string | null> {
  const swr = await registration();
  // `await navigator.serviceWorker.ready` matters on a cold first load: `getToken` needs an *active*
  // worker, and a registration that was created milliseconds ago is still `installing`.
  await navigator.serviceWorker.ready;
  const token = await getToken(messaging(), {
    vapidKey: VAPID_KEY,
    serviceWorkerRegistration: swr,
  });
  return token || null;
}

async function registerToken(eventId: string, token: string): Promise<void> {
  await authedFetch(`/v1/events/${eventId}/push-token`, {
    method: "POST",
    body: JSON.stringify({ token, platform: isIos() ? "ios" : "web" }),
  });
}

/** Ask for permission and subscribe. **Call only from a click.** Returns what actually happened, so
 * the caller can render the outcome rather than guessing from a boolean. */
export async function enablePush(eventId: string): Promise<PushSupport> {
  const support = pushSupport();
  if (!support.ok) return support;
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return { ok: false, reason: "blocked" };
    const token = await currentToken();
    if (!token) return { ok: false, reason: "unsupported" };
    await registerToken(eventId, token);
    return { ok: true };
  } catch {
    // A `getToken` failure is almost always a missing/incorrect VAPID key or a blocked push service,
    // and neither is something the guest can act on — so it reports as unsupported rather than as an
    // error with a stack trace in it.
    return { ok: false, reason: "unsupported" };
  }
}

/** Refresh the stored token if permission is already granted. Safe and intended to call on every app
 * open; a no-op when permission was never granted, so it never triggers a prompt. */
export async function syncPush(eventId: string): Promise<void> {
  if (!pushSupport().ok || Notification.permission !== "granted") return;
  try {
    const token = await currentToken();
    if (token) await registerToken(eventId, token);
  } catch {
    // Silent by design: this runs unattended on page load and a failed refresh means the guest keeps
    // whatever token the server already had. Nothing here is worth interrupting them for.
  }
}

/** Unsubscribe: delete the FCM token *and* forget it server-side. Browser permission itself stays
 * granted (only the guest can revoke that), so re-enabling later needs no second prompt. */
export async function disablePush(eventId: string): Promise<void> {
  try {
    await authedFetch(`/v1/events/${eventId}/push-token`, { method: "DELETE" });
  } catch {
    // The server side is the one that matters for delivery; a network failure here leaves a token
    // that the next send prunes anyway once `deleteToken` below has invalidated it.
  }
  try {
    if (pushSupport().ok) await deleteToken(messaging());
  } catch {
    // Already gone.
  }
}

/** Foreground messages. With data-only payloads the browser draws nothing while a tab is focused,
 * which is correct — `BountyBanner` is already on screen and a system notification on top of it
 * would be the same ask twice. Wired anyway so a foreground arrival can nudge the UI. */
export function onPushMessage(handler: (data: Record<string, string>) => void): () => void {
  if (!pushSupport().ok || !auth?.currentUser) return () => {};
  try {
    return onMessage(messaging(), (payload) => {
      handler((payload.data ?? {}) as Record<string, string>);
    });
  } catch {
    return () => {};
  }
}
