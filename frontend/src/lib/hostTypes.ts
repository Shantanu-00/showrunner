// Host console wire shapes — mirrors backend/schemas/host.py exactly (spec 08, spec 11 §1/§2/§6).
// Kept in its own file rather than growing lib/types.ts: the host console is a separate surface
// (spec 12 §8) with its own code-split bundle, and nothing here is read by the guest PWA or kiosk.

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

export interface EventStageDoc {
  stageId: string;
  label: string;
  startsAt?: string | null;
  endsAt?: string | null;
  requiredMoments: RequiredMoment[];
  theme?: string | null;
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
