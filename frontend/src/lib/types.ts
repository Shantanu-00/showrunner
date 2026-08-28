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

// ---------------------------------------------------------------------------
// Spec 03 §1/§3, spec 04, spec 11 §3 — mirrors backend/schemas/{media,person,event,common}.py.
// Only fields the guest PWA actually reads/renders are included.

/** Numeric ring, matches backend/schemas/common.py::ConsentRing (an int Enum). */
export const RING_VALUE: Record<ConsentRing, number> = { self: 0, pool: 1, public: 2 };

export type GuardianVerdict = "public_ok" | "private_only" | "host_review" | "blocked";
export type StageState = "pending" | "done" | "failed" | "failed_permanent";
export type MediaStatus =
  | "awaiting_upload"
  | "uploaded"
  | "processing"
  | "indexed"
  | "rejected"
  | "quarantined"
  | "abandoned";

export interface BoundingBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FaceRef {
  faceId: string;
  box: BoundingBox;
  personId?: string | null;
  clusterId?: string | null;
}

export interface CuratorBlock {
  stageId?: string | null;
  stagePosterior: Record<string, number>;
  visual: Record<string, number>;
  momentTags: string[];
  aestheticScore: number;
  quality: { blur?: number | null; exposure?: number | null; eyesClosed?: number | null };
  isHighlight: boolean;
  caption?: string | null;
  culturalElements: string[];
  peopleCountEstimate?: number | null;
  needsReview: boolean;
}

export interface GuardianBlock {
  verdict?: GuardianVerdict | null;
  reasons: string[];
}

/** `events/{eventId}/media/{mediaId}` — only the fields every guest surface renders. */
export interface MediaDoc {
  mediaId: string;
  uploaderUid: string;
  batchId: string;
  kind: MediaKind;
  bountyId?: string | null;

  status: MediaStatus;
  duplicateOf?: string | null;
  deleted: boolean;

  consent: { ring: ConsentRing };
  subjectVetoes: string[];
  visibility?: "self" | "pool" | "public" | null;

  curator?: CuratorBlock | null;
  guardian?: GuardianBlock | null;
  faces: FaceRef[];
  albumOf: string[];

  capturedAt?: string | null;
  uploadedAt?: string | null;
  createdAt?: string | null;

  thumbUri?: string | null;
  displayUri?: string | null;
  posterUri?: string | null;
  width?: number | null;
  height?: number | null;
}

/** Spec 11 §3 — VIP is policy (deterministic), not memory. Max-across-faces multiplier. */
export type Tier = 0 | 1 | 2 | 3;
export const VIP_WEIGHT: Record<Tier, number> = { 0: 3.0, 1: 1.8, 2: 1.3, 3: 1.0 };

/** `events/{eventId}/people/{personId}` — the fields the guest PWA needs. */
export interface PersonDoc {
  personId: string;
  displayName?: string | null;
  tier: Tier;
  featured: boolean;
  consent: { selfieEnrolled: boolean; enrolledAt?: string | null };
  createdAt?: string | null;
}

/** `GET /v1/events/{eventId}/public` — the narrow, non-sensitive event bootstrap. */
export interface EventPublicInfo {
  exists: boolean;
  eventId?: string;
  name?: string;
  status?: "draft" | "live" | "paused" | "wrapping" | "wrapped";
  timezone?: string;
  activeStage?: string | null;
  templateId?: string;
  stages?: Array<{ stageId: string; label: string; theme?: string | null }>;
  uploadsOpen?: boolean;
  publicFrozen?: boolean;
  serverTime?: string;
}

// ---------------------------------------------------------------------------
// Kiosk — `events/{eventId}/kiosk/playlist` (spec 04 §4). A dumb client only ever renders
// what the publisher already decided; slot `factors` (when present) are stored, not computed.

export interface SlotFactors {
  aesthetic: number;
  recency: number;
  diversity: number;
  stageMatch: number;
  vipWeight: number;
  rank: number;
}

export interface HeroSlot {
  type: "hero";
  mediaId: string;
  holdSec: number;
  factors?: SlotFactors;
}
export interface ReelSlot {
  type: "reel";
  reelId: string;
  premiere?: boolean;
}
export interface CollageSlot {
  type: "collage";
  collageId: string;
}
export interface LeaderboardSlot {
  type: "leaderboard";
  topN: number;
}
export interface BountyCallSlot {
  type: "bounty_call";
  bountyId: string;
}
export interface JustInSlot {
  type: "just_in";
  liveWindowSec: number;
  mediaIds?: string[];
}
export type KioskSlot =
  | HeroSlot
  | ReelSlot
  | CollageSlot
  | LeaderboardSlot
  | BountyCallSlot
  | JustInSlot;

export interface KioskPlaylist {
  revision: number;
  activeStageId?: string | null;
  theme?: string | null;
  slots: KioskSlot[];
  updatedAt?: string | null;
}

export interface LeaderboardEntry {
  uid: string;
  personId?: string | null;
  displayName?: string | null;
  points: number;
}

// ---------------------------------------------------------------------------
// Identity/claims — mirrors backend/schemas/identity.py exactly (spec 02 §3). The
// identity-granting mechanism is `set_custom_user_claims` on the *caller's own* uid, done
// server-side; there is no custom-token minting anywhere in this flow, so the client's only job
// after any of these calls is `refreshClaims()` (force-refresh the ID token).

export type EnrollOutcome = "linked" | "held_for_review" | "pending_host_approval";

export interface EnrollResponse {
  outcome: EnrollOutcome;
  personId?: string | null;
  displayName?: string | null;
  claimId?: string | null;
  claimedFaces: number;
  topSimilarity: number;
  customToken?: string | null; // always null today — kept for wire compatibility
  message: string;
}

export interface ClaimLinkResponse {
  url: string;
  code: string;
  expiresAt: string;
}

export interface RedeemResponse {
  eventId: string;
  personId?: string | null;
  customToken?: string | null; // always null today (see EnrollResponse note)
  displayName?: string | null;
}

export interface VisibilityResponse {
  mediaId: string;
  visibility: "self" | "pool" | "public" | null;
}

/** `events/{eventId}/bounties/{bountyId}` — spec 05 §3, mirroring `backend/schemas/bounty.py`.
 * `title` is the wanted-poster headline on the wall; `copy` is the sentence in a guest's pocket. */
export interface BountyDoc {
  bountyId: string;
  title: string;
  copy: string;
  points: number;
  status: "active" | "escalated" | "fulfilled" | "expired" | "cancelled";
  targetVipName?: string | null;
  audience?: "all" | "nearStage" | "topContributors";
  kioskTakeover?: boolean;
  expiresAt?: string | null;
}

/** `events/{eventId}/reels/{reelId}` (spec 06 — not built this session; same rationale as
 * `BountyDoc` above). */
export interface ReelDoc {
  reelId: string;
  title: string;
  videoUri?: string | null;
  status: "rendering" | "published" | "unpublished";
}
