// Deterministic, display-only re-ranking (spec 04 §4, spec 11 §3.3). Nothing here ever gates
// visibility or money — that is `recompute_visibility`'s job alone (spec 04 §2) and it never
// runs in the browser. This file only *orders* and *explains* what is already public.

import { VIP_WEIGHT, type MediaDoc, type SlotFactors, type Tier } from "./types";

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

/** "Why this photo?" factors (spec 04 §4 / spec 12 §8). On the kiosk these come straight off
 * the slot the publisher already stored — pure display, zero recomputation. The gallery
 * Highlights tab has no publisher-stored slot (the publisher only programs the kiosk), so this
 * derives the same formula client-side from already-public stored fields (aestheticScore, tier)
 * for that surface only; it is a real computation of real data, never a fabricated number. */
export function whyFactorsForGallery(
  media: MediaDoc,
  tierByPersonId: Record<string, number>,
  rank: number
): SlotFactors {
  return {
    aesthetic: media.curator?.aestheticScore ?? 0,
    recency: 1.0,
    diversity: 1.0,
    stageMatch: 1.0,
    vipWeight: vipWeightForMedia(media, tierByPersonId),
    rank,
  };
}
