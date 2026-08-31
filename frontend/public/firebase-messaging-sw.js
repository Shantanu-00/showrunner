/* Showrunner's push service worker.
 *
 * It imports **nothing**, and that is the interesting decision. The conventional way to do this is
 * three `importScripts('https://www.gstatic.com/firebasejs/…')` lines plus
 * `onBackgroundMessage`, which would mean: a runtime CDN dependency on the one code path that has to
 * work when the app is closed and the network is a hotel wifi, a Firebase SDK version pinned in a
 * file no bundler checks, and ~90 kB fetched before a notification can be drawn. This project
 * already refuses runtime CDNs for fonts (`next/font` self-hosts them); a notification that has to
 * reach somebody at dinner is a worse place to make an exception than a typeface.
 *
 * It does not need them. Firebase Cloud Messaging on the web *is* W3C Web Push underneath: the
 * `push` event carries the payload, and `registration.showNotification` draws it. The Firebase SW
 * SDK exists to give you `onBackgroundMessage` and automatic display of `notification` payloads —
 * conveniences, not capabilities. What we lose is nothing we use. What we gain is a file with no
 * dependencies, no version to drift, and no fetch between a buzz and a photograph.
 *
 * The filename is deliberately the conventional one. `getToken()` falls back to registering
 * `/firebase-messaging-sw.js` itself when no `serviceWorkerRegistration` is passed, so keeping the
 * name means the token path still works if `lib/push.ts` ever stops passing its own registration.
 *
 * The server sends **data-only** messages (`backend/shared/push.py`). A top-level `notification`
 * payload is drawn by the browser *and* handed to this worker on some engines, which is how one
 * bounty becomes two banners on the same phone. Data-only means display happens here, exactly once.
 */

/* global self, clients */

const FALLBACK_TITLE = "Showrunner";

self.addEventListener("install", () => {
  // Take over immediately rather than waiting for every tab to close. A guest who grants permission
  // and walks away must be reachable on the next bounty, not after their next full app restart.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  // Always show *something*. A `push` handler that resolves without calling `showNotification` makes
  // Chrome draw its own "This site has been updated in the background" notice, which is worse than
  // any message we could have written ourselves.
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    // A non-JSON payload should still buzz. Falling through with an empty object gets the generic
    // copy below, which is a poor notification but an honest one.
  }

  // FCM nests our fields under `data` for a data-only message; a `notification` payload (which we do
  // not send, but a future caller might) puts them at `notification`. Read all three shapes so this
  // file never becomes the reason a message arrives blank.
  const data = payload.data || {};
  const notification = payload.notification || {};
  const title = data.title || notification.title || FALLBACK_TITLE;
  const body = data.body || notification.body || "The director has a new request for you.";
  const link = data.link || (payload.fcmOptions && payload.fcmOptions.link) || "/";

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      // Collapse by bounty id: a re-send or a duplicate delivery replaces the banner instead of
      // stacking a second copy of the same ask. Cloud Tasks and FCM are both at-least-once, so this
      // is the client half of the idempotency the rest of the system takes seriously.
      tag: data.tag || "showrunner",
      renotify: false,
      // `requireInteraction: false` on purpose. A coverage gap has a TTL measured in minutes; a
      // banner that will not go away until it is tapped outlives the thing it was asking for.
      requireInteraction: false,
      data: { link },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/";

  event.waitUntil(
    (async () => {
      const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
      // Prefer an open tab on the same origin and navigate it, rather than opening a second copy of
      // the app: the guest almost certainly already has the join screen somewhere, and two tabs
      // holding two anonymous sessions on the same event is a confusing thing to hand somebody who
      // just wanted to take a photo.
      for (const client of windows) {
        if ("focus" in client) {
          try {
            if ("navigate" in client) await client.navigate(link);
            return await client.focus();
          } catch {
            // Cross-origin or a client that refuses navigation — fall through and open fresh.
          }
        }
      }
      return clients.openWindow(link);
    })()
  );
});
