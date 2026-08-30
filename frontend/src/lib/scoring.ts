// Deterministic, display-only re-ranking (spec 04 §4, spec 11 §3.3). Nothing here ever gates
// visibility or money — that is `recompute_visibility`'s job alone (spec 04 §2) and it never
// runs in the browser. This file only *orders* and *explains* what is already public.

import { VIP_WEIGHT, type MediaDoc, type SlotFactors, type Tier } from "./types";

// Mirrors `backend/shared/settings.py`'s kiosk constants, for the same reason and with the same
// caveat as `lib/kiosk.ts::slotHoldSec`: the publisher owns the real arithmetic, and these exist so
// the gallery's explanation card can show a *computed* number instead of a placeholder. If the
// backend values move, these must move with them — a transparency card that quietly disagrees with
// the ranking it claims to explain is worse than no card.
const RECENCY_HALF_LIFE_MIN = 20; // KIOSK_RECENCY_HALF_LIFE_MIN
const STAGE_MATCH_ACTIVE = 1.0; // KIOSK_STAGE_MATCH_ACTIVE
const STAGE_MATCH_PREVIOUS = 0.4; // KIOSK_STAGE_MATCH_PREVIOUS
const STAGE_MATCH_OTHER = 0.2; // KIOSK_STAGE_MATCH_OTHER

/** Max vipWeight across the faces in frame — a guest photographed with a Principal inherits
 * their ×3.0 (spec 04 §4's deliberate social-dynamic reward). Unrecognised/unclaimed faces
 * count as Guest (1.0), never inflate a score. */
export function vipWeightForMedia(
  media: MediaDoc,
  tierByPersonId: Record<string, number>
): number {
  let max = 1.0;
  for (const face of media.faces) {
    if (!face.personId) continue;
    const tier = (tierByPersonId[face.personId] ?? 3) as Tier;
    max = Math.max(max, VIP_WEIGHT[tier] ?? 1.0);
  }
  return max;
}

/** `0.5 ** (age / 20 min)` — `publisher/program.py::recency_decay`, verbatim.
 *
 * Indexed on `capturedAt` exactly as the publisher does, not on upload time: that is the whole
 * reason a photo taken hours ago and forwarded now scores low, and showing 1.00 here would hide
 * precisely the fact a viewer is asking about. A missing timestamp reads as "now" (1.0), matching
 * the publisher's own fallback — intake always writes one, so only a hand-seeded doc gets here. */
export function recencyForMedia(media: MediaDoc, now: number = Date.now()): number {
  const capturedAt = media.capturedAt ? Date.parse(media.capturedAt) : NaN;
  if (!Number.isFinite(capturedAt)) return 1.0;
  const ageMin = Math.max(0, (now - capturedAt) / 60_000);
  return 0.5 ** (ageMin / RECENCY_HALF_LIFE_MIN);
}

/** Active ×1.0, previous ×0.4, everything else ×0.2 — `publisher/program.py::stage_match`.
 *
 * "Previous" is the stage immediately before the active one in the event's own ordering, which is
 * the same thing the publisher means by it. With no active stage there is nothing to be near, so
 * every photo takes the `other` multiplier rather than a flattering default. */
export function stageMatchForMedia(
  media: MediaDoc,
  stages: Array<{ stageId: string }>,
  activeStageId: string | null | undefined
): number {
  const stageId = media.curator?.stageId ?? null;
  if (!activeStageId || !stageId) return STAGE_MATCH_OTHER;
  if (stageId === activeStageId) return STAGE_MATCH_ACTIVE;
  const activeIndex = stages.findIndex((s) => s.stageId === activeStageId);
  const previousId = activeIndex > 0 ? stages[activeIndex - 1].stageId : null;
  if (previousId && stageId === previousId) return STAGE_MATCH_PREVIOUS;
  return STAGE_MATCH_OTHER;
}

/** Highlights ordering (spec 04 §3): `aestheticScore × vipWeight`, a shared re-rank identical
 * for every viewer — never personalized per visitor. */
export function rankHighlights(
  items: MediaDoc[],
  tierByPersonId: Record<string, number>
): MediaDoc[] {
  return [...items].sort((a, b) => {
    const scoreA = (a.curator?.aestheticScore ?? 0) * vipWeightForMedia(a, tierByPersonId);
    const scoreB = (b.curator?.aestheticScore ?? 0) * vipWeightForMedia(b, tierByPersonId);
    return scoreB - scoreA;
  });
}

/** "Why this photo?" factors (spec 04 §4 / spec 12 §8).
 *
 * On the kiosk these come straight off the slot the publisher already stored — pure display, zero
 * recomputation, and therefore incapable of disagreeing with the decision. The gallery has no
 * publisher slot (the publisher only programs the kiosk), so this derives the same formula
 * client-side from already-public stored fields.
 *
 * **`diversity` is deliberately absent here, not defaulted to 1.0.** It is a property of a slot's
 * position in the kiosk's program — whether that slot had to reuse a face cluster inside the
 * five-slot window — and a masonry grid has no such window, so there is no honest value to show.
 * An earlier version of this function returned hard-coded `1.0` for `diversity`, `recency` *and*
 * `stageMatch`; those two are now computed, and this one is omitted so the card can say what it
 * does not know instead of inventing it. `rank` is 0-based, matching the publisher.
 */
export function whyFactorsForGallery(
  media: MediaDoc,
  tierByPersonId: Record<string, number>,
  rank: number,
  stages: Array<{ stageId: string }> = [],
  activeStageId: string | null | undefined = null
): SlotFactors {
  return {
    aesthetic: media.curator?.aestheticScore ?? 0,
    recency: recencyForMedia(media),
    stageMatch: stageMatchForMedia(media, stages, activeStageId),
    vipWeight: vipWeightForMedia(media, tierByPersonId),
    rank,
  };
}
