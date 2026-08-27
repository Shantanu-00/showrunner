// Mirrors spec 01 §3 / spec 02 §4's client-facing contract.
// Reconcile with backend/schemas/ once B1-S2 lands.

export type ConsentRing = "self" | "pool" | "public";

export interface BatchConsent {
  public: boolean;
  selfOnly: boolean;
}

export type MediaKind = "photo" | "video";

export type OutboxState =
  | "queued"
  | "url_issued"
  | "uploading"
  | "done"
  | "failed";

export interface OutboxItem {
  clientMediaId: string; // ULID
  blob: Blob;
  fileName: string;
  contentType: string;
  size: number;
  lastModified: number;
  eventId: string;
  batchId: string; // ULID per selection action
  consent: BatchConsent;
  bountyId?: string;
  kind: MediaKind;
  state: OutboxState;
  signedUrl?: string;
  urlExpiresAt?: number; // epoch ms
  resumableSessionUri?: string;
  bytesSent?: number;
  attempts: number;
  lastError?: string;
}

export interface DoneLedgerEntry {
  clientMediaId: string;
  thumbDataUrl: string;
}

// POST /v1/events/{eventId}/uploads
export interface UploadBatchRequestFile {
  clientMediaId: string;
  fileName: string;
  contentType: string;
  size: number;
  capturedAt?: string;
}

export interface UploadBatchRequest {
  batchId: string;
  consent: BatchConsent;
  bountyId?: string;
  files: UploadBatchRequestFile[];
}

export interface UploadBatchResponseItem {
  mediaId: string; // == clientMediaId
  kind: MediaKind;
  signedUrl?: string;
  resumableSessionUri?: string;
  expiresAt: string;
}

export interface UploadBatchResponse {
  uploads: UploadBatchResponseItem[];
}
