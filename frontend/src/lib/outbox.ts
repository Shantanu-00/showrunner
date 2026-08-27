import { openDB, type IDBPDatabase } from "idb";
import { newUlid } from "./ulid";
import type { BatchConsent, DoneLedgerEntry, MediaKind, OutboxItem, OutboxState } from "./types";

const DB_NAME = "showrunner-outbox";
const DB_VERSION = 1;
const STORE = "outbox";
const DONE_LEDGER_STORE = "doneLedger";

let dbPromise: Promise<IDBPDatabase> | null = null;

function getDb(): Promise<IDBPDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "clientMediaId" });
        }
        if (!db.objectStoreNames.contains(DONE_LEDGER_STORE)) {
          db.createObjectStore(DONE_LEDGER_STORE, { keyPath: "clientMediaId" });
        }
      },
    });
  }
  return dbPromise;
}

function kindFromContentType(contentType: string): MediaKind {
  return contentType.startsWith("video/") ? "video" : "photo";
}

export interface EnqueueOptions {
  eventId: string;
  batchId?: string;
  consent: BatchConsent;
  bountyId?: string;
}

/** Writes every selected file to the outbox synchronously, before any network call. */
export async function enqueue(files: File[], opts: EnqueueOptions): Promise<OutboxItem[]> {
  const db = await getDb();
  const batchId = opts.batchId ?? newUlid();
  const tx = db.transaction(STORE, "readwrite");
  const items: OutboxItem[] = files.map((file) => ({
    clientMediaId: newUlid(),
    blob: file,
    fileName: file.name,
    contentType: file.type,
    size: file.size,
    lastModified: file.lastModified,
    eventId: opts.eventId,
    batchId,
    consent: opts.consent,
    bountyId: opts.bountyId,
    kind: kindFromContentType(file.type),
    state: "queued",
    attempts: 0,
  }));
  await Promise.all(items.map((item) => tx.store.put(item)));
  await tx.done;
  return items;
}

export async function listAll(): Promise<OutboxItem[]> {
  const db = await getDb();
  return db.getAll(STORE);
}

export async function getItem(clientMediaId: string): Promise<OutboxItem | undefined> {
  const db = await getDb();
  return db.get(STORE, clientMediaId);
}

export async function putItem(item: OutboxItem): Promise<void> {
  const db = await getDb();
  await db.put(STORE, item);
}

export async function updateState(
  clientMediaId: string,
  state: OutboxState,
  patch: Partial<OutboxItem> = {}
): Promise<void> {
  const db = await getDb();
  const existing = await db.get(STORE, clientMediaId);
  if (!existing) return;
  await db.put(STORE, { ...existing, ...patch, state });
}

/** Moves a completed item off the hot outbox into a small done ledger for instant UI. */
export async function markDone(clientMediaId: string, thumbDataUrl: string): Promise<void> {
  const db = await getDb();
  const entry: DoneLedgerEntry = { clientMediaId, thumbDataUrl };
  const tx = db.transaction([STORE, DONE_LEDGER_STORE], "readwrite");
  await tx.objectStore(DONE_LEDGER_STORE).put(entry);
  await tx.objectStore(STORE).delete(clientMediaId);
  await tx.done;
}

export async function listDoneLedger(): Promise<DoneLedgerEntry[]> {
  const db = await getDb();
  return db.getAll(DONE_LEDGER_STORE);
}
