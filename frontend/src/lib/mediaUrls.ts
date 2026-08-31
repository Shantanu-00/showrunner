"use client";

// Where a photograph's bytes actually come from on this client, and where they stay.
//
// Two problems used to live in `lib/useAuthedImage.ts` + `lib/mediaCache.ts`, and they were the same
// problem wearing different clothes:
//
// 1. **Nothing private ever rendered.** Any tier above `public` — and *every* tier on an invite-only
//    event — fetched `…/media/{id}/render` with an `Authorization` header, which makes the request
//    non-simple, which makes the browser preflight it, and the endpoint's 302 then carried that
//    preflighted request onto `storage.googleapis.com`, where no bucket CORS policy can allow a
//    request header it was never configured with. Preflight failed → fetch blocked → a matched album
//    of empty tiles with "93% Match" badges over them. `?json=1` (`api/media.py`) splits the hops:
//    the *authorized* one talks only to our own API, and the bytes hop is a bare `<img src>`, which
//    is not a CORS request at all.
// 2. **Every visit re-downloaded everything.** Blob URLs live in a page's memory, so switching tabs
//    or reopening the PWA started from nothing: shimmering skeletons for photographs the phone had
//    already downloaded twice that hour. A trip's photos do not change once they are indexed, so
//    they belong in IndexedDB, keyed by (event, media, variant), and read back before the network is
//    consulted at all.
//
// The order every consumer gets, therefore: **memory → IndexedDB → signed URL**, with the byte copy
// written back in the background. First paint on a warm cache is synchronous; on a cold one it is a
// direct GCS image load with nothing in front of it.

import { authedJson } from "./api";

/** `event:media:variant`. Stable across sessions — that is the whole point of it. */
export type CacheKey = string;

export function cacheKeyFor(eventId: string, mediaId: string, variant: string): CacheKey {
  return `${eventId}:${mediaId}:${variant}`;
}

/* ------------------------------------------------------------------ signed URLs (short-lived) */

interface UrlEntry {
  url: string;
  expiresAtMs: number;
}

const urlCache = new Map<string, UrlEntry>();
const urlInflight = new Map<string, Promise<string | null>>();

/** Ask the API for the signed URL behind an authed media path, instead of following its 302.
 *
 * Cached until shortly before it actually expires: a 60-minute URL re-requested per tile per scroll
 * would put the round trip back that `?json=1` just removed. */
export async function resolveSignedUrl(apiPath: string): Promise<string | null> {
  const hit = urlCache.get(apiPath);
  if (hit && hit.expiresAtMs > Date.now()) return hit.url;

  const pending = urlInflight.get(apiPath);
  if (pending) return pending;

  const sep = apiPath.includes("?") ? "&" : "?";
  const task = (async () => {
    try {
      const body = await authedJson<{ url: string; expiresInSec?: number }>(
        `${apiPath}${sep}json=1`,
        { method: "GET" }
      );
      if (!body?.url) return null;
      // One minute of headroom, so a URL is never handed out with seconds left on it.
      const ttl = Math.max(60, (body.expiresInSec ?? 3600) - 60);
      urlCache.set(apiPath, { url: body.url, expiresAtMs: Date.now() + ttl * 1000 });
      return body.url;
    } catch {
      return null;
    } finally {
      urlInflight.delete(apiPath);
    }
  })();
  urlInflight.set(apiPath, task);
  return task;
}

/* ------------------------------------------------------------------ the byte cache (IndexedDB) */

const DB_NAME = "showrunner-media";
const DB_VERSION = 1;
const STORE = "bytes";
/** Roughly a full multi-day trip's thumbnails plus a few hundred displays. Thumbs are ~15 KB, so
 * this is single-digit megabytes in practice — and eviction is oldest-first, so the current day
 * always wins over the first one. */
const MAX_ENTRIES = 600;

interface ByteRecord {
  key: CacheKey;
  blob: Blob;
  at: number;
}

/** `key → object URL`. Object URLs are per-document, so this is rebuilt each page load from the
 * durable blobs below; it is what makes a second visit to a tab paint with no await at all. */
const objectUrls = new Map<CacheKey, string>();
const hydrating = new Map<CacheKey, Promise<string | null>>();

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "key" });
          store.createIndex("at", "at");
        }
      };
      req.onsuccess = () => resolve(req.result);
      // Private-browsing modes and storage-pressure failures are not errors worth surfacing: the
      // whole file degrades to "fetch it from GCS like before".
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

function idbGet(db: IDBDatabase, key: CacheKey): Promise<ByteRecord | null> {
  return new Promise((resolve) => {
    try {
      const req = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
      req.onsuccess = () => resolve((req.result as ByteRecord | undefined) ?? null);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** The synchronous read every consumer tries first. */
export function cachedObjectUrl(key: CacheKey | null | undefined): string | null {
  if (!key) return null;
  return objectUrls.get(key) ?? null;
}

/** The durable read: an object URL for bytes this device already holds, or null. */
export async function hydrateFromCache(key: CacheKey): Promise<string | null> {
  const warm = objectUrls.get(key);
  if (warm) return warm;
  const pending = hydrating.get(key);
  if (pending) return pending;

  const task = (async () => {
    try {
      const db = await openDb();
      if (!db) return null;
      const record = await idbGet(db, key);
      if (!record?.blob) return null;
      const url = URL.createObjectURL(record.blob);
      objectUrls.set(key, url);
      return url;
    } catch {
      return null;
    } finally {
      hydrating.delete(key);
    }
  })();
  hydrating.set(key, task);
  return task;
}

let pruneScheduled = false;

/** Oldest-first eviction, run at most once per idle window rather than per write. */
function schedulePrune(): void {
  if (pruneScheduled) return;
  pruneScheduled = true;
  const run = () => {
    pruneScheduled = false;
    void (async () => {
      const db = await openDb();
      if (!db) return;
      try {
        const tx = db.transaction(STORE, "readwrite");
        const store = tx.objectStore(STORE);
        const countReq = store.count();
        countReq.onsuccess = () => {
          const excess = countReq.result - MAX_ENTRIES;
          if (excess <= 0) return;
          let dropped = 0;
          const cursorReq = store.index("at").openCursor();
          cursorReq.onsuccess = () => {
            const cursor = cursorReq.result;
            if (!cursor || dropped >= excess) return;
            const key = (cursor.value as ByteRecord).key;
            const stale = objectUrls.get(key);
            if (stale) {
              URL.revokeObjectURL(stale);
              objectUrls.delete(key);
            }
            cursor.delete();
            dropped += 1;
            cursor.continue();
          };
        };
      } catch {
        // A prune that fails leaves a slightly larger cache, which is not a failure state.
      }
    })();
  };
  if (typeof requestIdleCallback === "function") requestIdleCallback(run, { timeout: 4000 });
  else setTimeout(run, 2000);
}

/** Download `url` once and keep the bytes for next time. Best-effort in every direction: a bucket
 * whose CORS policy refuses this plain GET simply means the `<img src>` keeps working and nothing is
 * cached, which is the behaviour that existed before this file. */
export async function cacheBytes(key: CacheKey, url: string): Promise<string | null> {
  if (objectUrls.has(key)) return objectUrls.get(key) ?? null;
  try {
    const res = await fetch(url, { credentials: "omit", mode: "cors" });
    if (!res.ok) return null;
    const blob = await res.blob();
    if (blob.size === 0) return null;
    const db = await openDb();
    if (db) {
      try {
        db.transaction(STORE, "readwrite")
          .objectStore(STORE)
          .put({ key, blob, at: Date.now() } satisfies ByteRecord);
        schedulePrune();
      } catch {
        // Quota exceeded — the object URL below is still worth having for this session.
      }
    }
    const objectUrl = URL.createObjectURL(blob);
    objectUrls.set(key, objectUrl);
    return objectUrl;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ save / share */

/** The full-size bytes behind an authed media path, for the two things that genuinely need a `Blob`:
 * the Web Share sheet (which takes `File`s) and a download.
 *
 * Resolves the signed URL first for the reason the whole module exists — fetching the API path with a
 * token cannot follow its own redirect. */
export async function mediaBlob(apiPath: string): Promise<Blob | null> {
  const url = await resolveSignedUrl(apiPath);
  if (!url) return null;
  try {
    const res = await fetch(url, { credentials: "omit", mode: "cors" });
    if (!res.ok) return null;
    return await res.blob();
  } catch {
    return null;
  }
}

/** Save one photo to the device. Falls back to opening the signed URL in a tab, which is the honest
 * behaviour when a browser refuses the cross-origin read: the guest still gets their photograph, with
 * one extra tap, instead of a button that appears to do nothing. */
export async function saveMediaToDisk(apiPath: string, filename: string): Promise<boolean> {
  const blob = await mediaBlob(apiPath);
  if (!blob) {
    const url = await resolveSignedUrl(apiPath);
    if (!url) return false;
    window.open(url, "_blank", "noopener");
    return true;
  }
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
  return true;
}

/** Warm the browser's own HTTP cache without allocating a blob — used for the public path, where the
 * later `<img src>` is the identical URL and therefore a cache hit. */
export function warmHttpCache(url: string): void {
  if (typeof window === "undefined") return;
  const img = new Image();
  img.decoding = "async";
  img.src = url;
  void img.decode?.().catch(() => {});
}
