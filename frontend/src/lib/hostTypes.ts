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

/** Repurposed (spec 13 pivot) from the wizard's old template-grid labels into the Settings panel's
 * "start from a preset…" `<select>` labels — neutral wording, never shown as a card grid, never
 * defaulted. Creation itself is itinerary-first and never sends `templateId` (`hostApi.createEvent`);
 * a host who wants the sensitivity dials/glossary pre-filled reaches these from the console instead. */
export const TEMPLATE_LABELS: Record<EventTemplateId, string> = {
  wedding_generic: "Generic Wedding",
  wedding_hindu: "Hindu Wedding",
  wedding_christian: "Christian Wedding",
  wedding_muslim: "Muslim Wedding",
  bachelor_bachelorette: "Bachelor / Bachelorette",
  birthday: "Birthday",
  graduation: "Graduation",
  corporate_offsite: "Corporate Offsite",
  custom: "Custom",
};

/** Order the preset `<select>` lists them in — matches the task's neutral ordering, `custom` last. */
export const TEMPLATE_PRESET_ORDER: EventTemplateId[] = [
  "wedding_generic",
  "wedding_hindu",
  "wedding_christian",
  "wedding_muslim",
  "bachelor_bachelorette",
  "birthday",
  "graduation",
  "corporate_offsite",
  "custom",
];

/** Client-side mirror of `backend/schemas/host.py::EVENT_TEMPLATE_DEFAULTS` — there is no GET for
 * the server's table, and the Settings panel's preset picker (spec 13) prefills the form *locally*
 * before the host ever saves, exactly like the old wizard step it replaced ("editable, not silently
 * authoritative", spec 11 §2). Keep in sync with `schemas/host.py` by hand; both sides are small and
 * this file says so. */
export const EVENT_TEMPLATE_PRESETS: Record<
  EventTemplateId,
  { vipTopology: "pyramid" | "flat"; sensitivityProfile: SensitivityProfile; culturalGlossary: string[] }
> = {
  wedding_generic: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "context_dependent", alcohol: "public_ok", attire: "standard" },
    culturalGlossary: ["vows", "ring exchange", "first dance", "bouquet toss"],
  },
  wedding_hindu: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "context_dependent", alcohol: "context_dependent", attire: "standard" },
    culturalGlossary: ["haldi", "sangeet", "pheras", "kanyadaan", "vidaai"],
  },
  wedding_christian: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "public_ok", alcohol: "public_ok", attire: "standard" },
    culturalGlossary: ["processional", "vows", "ring exchange", "first dance"],
  },
  wedding_muslim: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "public_ok", alcohol: "context_dependent", attire: "standard" },
    culturalGlossary: ["nikah", "rukhsati", "walima"],
  },
  bachelor_bachelorette: {
    vipTopology: "flat",
    sensitivityProfile: { pda: "public_ok", alcohol: "public_ok", attire: "relaxed" },
    culturalGlossary: [],
  },
  birthday: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "public_ok", alcohol: "context_dependent", attire: "standard" },
    culturalGlossary: ["cake cutting", "toast"],
  },
  graduation: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "public_ok", alcohol: "private_only", attire: "standard" },
    culturalGlossary: ["stage crossing", "family portrait"],
  },
  corporate_offsite: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "private_only", alcohol: "context_dependent", attire: "conservative" },
    culturalGlossary: ["keynote", "team sessions"],
  },
  custom: {
    vipTopology: "pyramid",
    sensitivityProfile: { pda: "context_dependent", alcohol: "context_dependent", attire: "standard" },
    culturalGlossary: [],
  },
};

/** Stage palette hint (spec 04 §4) — a closed, small vocabulary rather than a free color picker, so
 * the kiosk theme layer only ever has to know eight names. */
export const STAGE_THEME_OPTIONS = [
  "gold",
  "violet",
  "crimson",
  "ocean",
  "forest",
  "neon",
  "slate",
  "sunrise",
] as const;
export type StageTheme = (typeof STAGE_THEME_OPTIONS)[number];

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
  /** Present only when the event was created invite-only — the plaintext is never stored (only its
   * sha256), so this response is the one time it is ever readable (`schemas/host.py::CreateEventResponse`). */
  joinCode: string | null;
}

export interface HostLinkResponse {
  url: string;
  code: string;
  expiresAt: string;
}

/** `POST …/people/host-enroll` (spec 13 §7) — mirrors `backend/schemas/identity.py::HostEnrollResponse`.
 * Grants no identity by itself (see `hostApi.hostEnrollPerson`'s doc comment); it only confirms the
 * person document now exists with the tier the host chose. */
export interface HostEnrollResponse {
  personId: string;
  displayName: string;
  tier: number;
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
  /** "YYYY-MM-DDTHH:MM" **local wall-clock** in the event's own timezone, or "" when the source gave
   * no anchorable date (`api/host.py::_coerce_proposed_instants`). Feeds the review table's
   * `datetime-local` pickers directly — no timezone math on the client, because the value is already
   * expressed in the event's own local time. */
  proposedStartLocal: string;
  proposedEndLocal: string;
}

export interface ItineraryParseOut {
  stages: ParsedStage[];
  warnings: string[];
  /** The schedule text the model transcribed out of a PDF/screenshot, verbatim — empty when the
   * input was already pasted text. Not rendered anywhere load-bearing; kept for parity with the
   * backend response shape. */
  sourceText: string;
}

/** What `POST …/itinerary/parse`'s `fileMime` may be — mirrors `schemas/host.py::ITINERARY_FILE_MIMES`. */
export type ItineraryFileMime = "application/pdf" | "image/jpeg" | "image/png" | "image/webp";

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
  /** ISO local dates ("2026-10-12") in `timezone`, not UTC instants (spec 13) — `null`/absent means
   * "no day structure", the pre-spec-13 default every event still degrades to. See
   * `backend/schemas/event.py::Event.startsOn` and `dayIndexFromLocalDate` below, which is this
   * file's client-side mirror of `backend/shared/eventtime.py`. */
  startsOn?: string | null;
  endsOn?: string | null;
  expectedParticipants?: number | null;
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

/** A stable, host-editable stage/moment id from its label — shared by `ItineraryPanel` and the
 * wizard's review step, both of which mint a `stageId`/`momentId` for a row the host typed or
 * renamed by hand rather than one the parser already assigned. */
export function slugify(label: string): string {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "stage";
}

// ---------------------------------------------------------------------------
// Day-grouping (spec 13) — the client mirror of `backend/shared/eventtime.py`, deliberately narrow.
//
// Every stage-editing surface (the wizard's review step, `ItineraryPanel`) holds its working
// `startsAt`/`proposedStartLocal` values as a bare "YYYY-MM-DDTHH:MM" **local** string — the exact
// value a `datetime-local` input wants, and (for stages loaded from a saved UTC `startsAt`) the same
// naive `.slice(0, 16)` truncation `ItineraryPanel.tsx` already used before spec 13. That naive
// treatment is kept deliberately, not corrected here: inventing real per-string timezone conversion
// in this file would silently disagree with whatever convention the picker itself already commits
// to on save. Day-grouping only ever needs the *date* portion of that same string, so it inherits
// whichever convention produced it, correct or naive, without knowing the difference.

/** 1-based day index of a "YYYY-MM-DD…" local string against the event's own `startsOn` local date,
 * or `null` when either side is missing/unparseable — an undated event or an unscheduled stage gets
 * no day header, never a wrong one. Both sides are compared as UTC midnight purely so the day-count
 * arithmetic can't be nudged a day by the *browser's* zone; this is not a claim about the event's
 * real timezone. */
export function dayIndexFromLocalDate(
  startsOn: string | null | undefined,
  localDateTime: string | null | undefined
): number | null {
  if (!startsOn || !localDateTime) return null;
  const datePart = localDateTime.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart) || !/^\d{4}-\d{2}-\d{2}$/.test(startsOn)) return null;
  const start = new Date(`${startsOn}T00:00:00Z`);
  const day = new Date(`${datePart}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(day.getTime())) return null;
  return Math.round((day.getTime() - start.getTime()) / 86_400_000) + 1;
}

/** The calendar date ("YYYY-MM-DD") that `dayIndex` (1-based) falls on, given the event's `startsOn`. */
export function dateForDayIndex(startsOn: string, dayIndex: number): string {
  const d = new Date(`${startsOn}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + (dayIndex - 1));
  return d.toISOString().slice(0, 10);
}

/** `"Wed, Oct 15"` from a "YYYY-MM-DD" string — read as UTC midnight so the *label* can't drift a day
 * from the browser's own zone either, matching `dayIndexFromLocalDate`'s convention. */
export function formatLocalDate(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
