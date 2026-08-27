import * as outbox from "./outbox";
import { registerUploadBatch, refreshUploadUrl } from "./api";
import type { OutboxItem } from "./types";

const MAX_CONCURRENT = 3;
const MAX_ATTEMPTS = 5;
const BATCH_SIZE = 50;
const VIDEO_CHUNK = 256 * 1024 * 64; // multiple of 256 KiB

let draining = false;
let listeners: Array<() => void> = [];

/** Components subscribe to be re-rendered whenever any outbox item changes state. */
export function onOutboxChange(cb: () => void): () => void {
  listeners.push(cb);
  return () => {
    listeners = listeners.filter((l) => l !== cb);
  };
}

function notify() {
  listeners.forEach((cb) => cb());
}

function backoffMs(attempts: number): number {
  return Math.min(1000 * 2 ** attempts, 30_000);
}

async function fileToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

/** Groups queued items by (eventId, batchId) and registers them for signed URLs. */
async function issueUrls(items: OutboxItem[]): Promise<void> {
  const queued = items.filter((i) => i.state === "queued");
  if (queued.length === 0) return;

  const groups = new Map<string, OutboxItem[]>();
  for (const item of queued) {
    const key = `${item.eventId}::${item.batchId}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }

  for (const group of groups.values()) {
    for (let i = 0; i < group.length; i += BATCH_SIZE) {
      const chunk = group.slice(i, i + BATCH_SIZE);
      try {
        const res = await registerUploadBatch(chunk[0].eventId, {
          batchId: chunk[0].batchId,
          consent: chunk[0].consent,
          bountyId: chunk[0].bountyId,
          files: chunk.map((f) => ({
            clientMediaId: f.clientMediaId,
            fileName: f.fileName,
            contentType: f.contentType,
            size: f.size,
          })),
        });
        for (const upload of res.uploads) {
          await outbox.updateState(upload.mediaId, "url_issued", {
            signedUrl: upload.signedUrl,
            resumableSessionUri: upload.resumableSessionUri,
            urlExpiresAt: Date.parse(upload.expiresAt),
          });
        }
      } catch (err) {
        // api service isn't live until B1-S2 ships — surface as retryable failures,
        // never crash the outbox drain loop.
        for (const item of chunk) {
          await bumpFailure(item, err);
        }
      }
    }
  }
}

async function bumpFailure(item: OutboxItem, err: unknown): Promise<void> {
  const attempts = item.attempts + 1;
  const lastError = err instanceof Error ? err.message : String(err);
  if (attempts >= MAX_ATTEMPTS) {
    await outbox.updateState(item.clientMediaId, "failed", { attempts, lastError });
  } else {
    await outbox.updateState(item.clientMediaId, "queued", { attempts, lastError });
    setTimeout(() => void drain(), backoffMs(attempts));
  }
  notify();
}

async function ensureFreshUrl(item: OutboxItem): Promise<OutboxItem> {
  if (item.urlExpiresAt && item.urlExpiresAt < Date.now()) {
    const refreshed = await refreshUploadUrl(item.eventId, item.clientMediaId);
    const updated: OutboxItem = {
      ...item,
      signedUrl: refreshed.signedUrl,
      resumableSessionUri: refreshed.resumableSessionUri,
      urlExpiresAt: Date.parse(refreshed.expiresAt),
    };
    await outbox.putItem(updated);
    return updated;
  }
  return item;
}

async function uploadPhoto(item: OutboxItem): Promise<void> {
  if (!item.signedUrl) throw new Error("no signed URL issued for photo");
  const res = await fetch(item.signedUrl, {
    method: "PUT",
    headers: { "Content-Type": item.contentType },
    body: item.blob,
  });
  if (!res.ok) throw new Error(`PUT failed: ${res.status}`);
}

/** Resumable video upload per spec 01 §2.2 — structural implementation, not this
 * session's smoke-test focus (photo path is). */
async function uploadVideo(item: OutboxItem): Promise<void> {
  if (!item.resumableSessionUri) throw new Error("no resumable session issued for video");
  let sent = item.bytesSent ?? 0;
  const total = item.size;

  while (sent < total) {
    const end = Math.min(sent + VIDEO_CHUNK, total);
    const chunk = item.blob.slice(sent, end);
    const res = await fetch(item.resumableSessionUri, {
      method: "PUT",
      headers: {
        "Content-Range": `bytes ${sent}-${end - 1}/${total}`,
      },
      body: chunk,
    });

    if (res.status === 410) {
      // Session expired mid-upload — caller retries from a fresh registration.
      throw new Error("resumable session gone (410)");
    }
    if (res.status === 308 || res.ok) {
      sent = end;
      await outbox.updateState(item.clientMediaId, "uploading", { bytesSent: sent });
      continue;
    }
    throw new Error(`resumable PUT failed: ${res.status}`);
  }
}

async function uploadOne(item: OutboxItem): Promise<void> {
  await outbox.updateState(item.clientMediaId, "uploading");
  notify();
  try {
    const fresh = await ensureFreshUrl(item);
    if (fresh.kind === "video") {
      await uploadVideo(fresh);
    } else {
      await uploadPhoto(fresh);
    }
    const thumbDataUrl = fresh.kind === "photo" ? await fileToDataUrl(fresh.blob) : "";
    await outbox.markDone(item.clientMediaId, thumbDataUrl);
  } catch (err) {
    const latest = (await outbox.getItem(item.clientMediaId)) ?? item;
    await bumpFailure(latest, err);
    return;
  }
  notify();
}

/** Drains the outbox: issues URLs for queued items, then uploads url_issued items
 * with a concurrency cap. Safe to call repeatedly/concurrently — re-entrant no-ops. */
export async function drain(): Promise<void> {
  if (draining) return;
  draining = true;
  try {
    let items = await outbox.listAll();
    await issueUrls(items);

    items = await outbox.listAll();
    const uploadable = items.filter((i) => i.state === "url_issued");
    for (let i = 0; i < uploadable.length; i += MAX_CONCURRENT) {
      const slice = uploadable.slice(i, i + MAX_CONCURRENT);
      await Promise.all(slice.map(uploadOne));
    }
  } finally {
    draining = false;
  }
}

let triggersInstalled = false;

/** Wires the resume triggers from spec 01 §2.1: app open, visibility, online, 15s tick. */
export function installResumeTriggers(): () => void {
  if (triggersInstalled) return () => {};
  triggersInstalled = true;

  const onVisible = () => {
    if (document.visibilityState === "visible") void drain();
  };
  const onOnline = () => void drain();
  const interval = setInterval(() => void drain(), 15_000);

  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("online", onOnline);
  void drain();

  return () => {
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("online", onOnline);
    clearInterval(interval);
    triggersInstalled = false;
  };
}

export async function retryItem(clientMediaId: string): Promise<void> {
  await outbox.updateState(clientMediaId, "queued", { attempts: 0, lastError: undefined });
  void drain();
}
