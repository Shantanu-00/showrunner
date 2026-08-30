// Host console wire shapes — mirrors backend/schemas/host.py exactly (spec 08, spec 11 §1/§2/§6).
// Kept in its own file rather than growing lib/types.ts: the host console is a separate surface
// (spec 12 §8) with its own code-split bundle, and nothing here is read by the guest PWA or kiosk.

// The one exception to "nothing shared": `GuardianVerdict` is a backend enum
// (`schemas/common.py::GuardianVerdict`) that both surfaces read, and a second copy of a
// four-member union is a second thing to forget when the enum grows. A type-only import erases at
// compile time, so the code-split bundle is unaffected.
import type { GuardianVerdict } from "./types";

export type EventStatus = "draft" | "live" | "paused" | "wrapping" | "wrapped";

export type EventTemplateId =
  | "wedding_generic"
  | "wedding_hindu"
  | "wedding_christian"
  | "wedding_muslim"
  | "bachelor_bachelorette"
  | "birthday"
  | "graduation"
  | "corporate_offsite"
  | "custom";

export const TEMPLATE_LABELS: Record<EventTemplateId, string> = {
  wedding_generic: "Wedding",
  wedding_hindu: "Hindu Wedding",
  wedding_christian: "Christian Wedding",
  wedding_muslim: "Muslim Wedding",
  bachelor_bachelorette: "Bachelor(ette) Party",
  birthday: "Birthday",
  graduation: "Graduation",
  corporate_offsite: "Corporate Offsite",
  custom: "Custom",
};

export interface RequiredMoment {
  momentId: string;
  label: string;
  tierWeight?: number;
}

/** The closed scene vocabulary — mirrors `backend/schemas/common.py::SceneSetting`.
 *
 * The host only ever declares the six that describe a *place*. `closeup_detail`, `screen_or_document`
 * and `unknown` exist in the backend enum because the Curator needs to be able to say them about a
 * photo, but "this stage happens in a closeup" is not a thing a host can mean. */
export type ExpectedSetting =
  | "indoor_venue"
  | "outdoor_venue"
  | "outdoor_nature"
  | "domestic_interior"
  | "vehicle"
  | "street";

export const EXPECTED_SETTING_LABELS: Record<ExpectedSetting, string> = {
  indoor_venue: "Indoors — a hall or marquee",
  outdoor_venue: "Outdoors — a lawn or terrace",
  outdoor_nature: "Out in nature",
  domestic_interior: "A home or hotel room",
  vehicle: "In or around a vehicle",
  street: "On a street or road",
};

export interface EventStageDoc {
  stageId: string;
  label: string;
  startsAt?: string | null;
  endsAt?: string | null;
  requiredMoments: RequiredMoment[];
  theme?: string | null;
  /** Where the host says this stage happens. Optional, and empty is the common answer.
   *
   * It is the kiosk's cold-start prior for relevance: without it, a wedding that starts indoors and
   * moves outdoors for the baraat has `outdoor_venue` at 0% of the corpus when the baraat begins, so
   * the most important sequence of the day would read as an outlier and be demoted on the wall.
   * Declared by the host rather than guessed, so it cannot be a wrong assumption about their event. */
  expectedSetting?: ExpectedSetting | null;
}

export interface SensitivityProfile {
  pda: "public_ok" | "context_dependent" | "private_only";
  alcohol: "public_ok" | "context_dependent" | "private_only";
  attire: "relaxed" | "standard" | "conservative";
}

export interface EventTypeProfile {
  templateId: EventTemplateId;
  vipTopology: "pyramid" | "flat";
  sensitivityProfile: SensitivityProfile;
  culturalGlossary: string[];
  requiredMomentsTemplate: RequiredMoment[];
}

export interface CreateEventResponse {
  eventId: string;
  hostLink: string;
  recoveryCode: string;
}

export interface HostLinkResponse {
  url: string;
  code: string;
  expiresAt: string;
}

export interface RedeemHostResponse {
  eventId: string;
  eventName?: string | null;
}

export interface ParsedMoment {
  momentId: string;
  label: string;
}

export interface ParsedStage {
  stageId: string;
  label: string;
  orderIndex: number;
  timeHint: string;
  requiredMoments: ParsedMoment[];
  /** Already coerced to the vocabulary (or "") by `api/host.py::parse_itinerary`, so anything the
   * model invented has been dropped before it reaches the review table. A proposal, not a decision. */
  expectedSetting?: ExpectedSetting | "";
}

export interface ItineraryParseOut {
  stages: ParsedStage[];
  warnings: string[];
}

export interface LifecycleResponse {
  eventId: string;
  status: EventStatus;
  liveAt?: string | null;
  wrappedAt?: string | null;
}

export interface StageGap {
  stageId: string;
  stageLabel: string;
  momentId: string;
  momentLabel: string;
}

export interface StageReportRow {
  stageId: string;
  label: string;
  photoCount: number;
  highlightCount: number;
  meanAesthetic: number;
}

export interface Contributor {
  uid: string;
  displayName?: string | null;
  points: number;
}

export interface WrapReport {
  eventId: string;
  generatedAt: string;
  headline: string;
  totalPhotos: number;
  totalReels: number;
  totalPhotographers: number;
  perStage: StageReportRow[];
  honestGaps: StageGap[];
  topContributors: Contributor[];
}

/** `ledger/directorState` (spec 05 §1) — read live, host-only, purely for the next-tick
 * countdown (EXECUTION-PLAN §7e row 11): real evidence the Scheduler is firing unprompted,
 * not a value the client invents. */
export interface DirectorTickState {
  lastTickAtMs: number | null;
  tickCount: number;
}

export interface ConsoleSummary {
  eventId: string;
  status: EventStatus;
  photos: number;
  guests: number;
  coveragePct: number;
  costSoFarUsd: number;
  publicFrozen: boolean;
  liveEventCount?: number | null;
  /** Photos waiting on the host's decision, and photos the explicit-content gate blocked. Both come
   * from the same predicate the review-queue endpoint lists by, so the badge cannot show a count the
   * panel then fails to produce. */
  reviewCount: number;
  blockedCount: number;
}

/** One row of `GET …/media/review-queue` — see `backend/schemas/moderation.py::ReviewQueueItem`.
 * `modelVerdict` is what the Guardian said; a host decision is written to a *separate* field, so the
 * two stay side by side on the record. */
export interface ReviewQueueItem {
  mediaId: string;
  kind: "photo" | "video";
  modelVerdict: GuardianVerdict | null;
  reasons: string[];
  note: string | null;
  ritualEmotion: boolean;
  caption: string | null;
  aestheticScore: number;
  visibility: string | null;
  uploadedAt: string | null;
  offTopicNote: string | null;
}

export interface ReviewQueueResponse {
  eventId: string;
  verdict: GuardianVerdict;
  items: ReviewQueueItem[];
  truncated: boolean;
}

/** Full event doc, as the host reads it directly via `onSnapshot` (rules: `allow read: if
 * isHost(eventId)`) — the console's own live view, distinct from the guest-facing
 * `GET /v1/events/{eventId}/public` projection. */
export interface HostEventDoc {
  eventId: string;
  name: string;
  timezone: string;
  status: EventStatus;
  class?: "protected_demo" | "internal_dev" | "public";
  stages: EventStageDoc[];
  activeStage?: string | null;
  stageOverride?: string | null;
  eventTypeProfile: EventTypeProfile;
  publicFloor: number;
  publicFrozen: boolean;
  costSoFarUsd: number;
  wrapReport?: WrapReport | null;
  /** The door (spec 02 §1's event boundary), host-settable via `POST …/access*`. Only the code's
   * sha256 is ever stored, so a console can show *that* a code exists and when it was rotated, never
   * the code itself — the plaintext is returned once, at mint time, and cannot be re-read. */
  access?: {
    mode?: "open" | "invite";
    /** Seats, not people: the cap counts uids and one person routinely holds several (spec 02 §1). */
    maxGuests?: number | null;
    codeHash?: string | null;
    codeRotatedAt?: string | null;
    /** Honoured by the kiosk client, not by a security rule — `kiosk/{document}` is
     * `allow read: if true` (spec 09 §3) and a rule cannot read this without a `get()`. */
    kioskPublic?: boolean;
  };
  /** Seats taken. Incremented transactionally by `POST /join`, in the same transaction as the
   * `guests/{uid}` create, so the counter and the roster cannot disagree. */
  guestCount?: number;
}
