# Spec 07 — Personalization & Memory (swipe interface, taste profiles)

Goal: the private album learns each person's taste from a Tinder-style swipe, and that taste shapes their album ordering and their personal reel — turning "Collaborative Partner"-grade adaptivity into a Taskmaster feature (agents adapt silently; the user never fills a form).

## 1. The swipe surface (private album)

- Private album has a **"Curate my album"** mode: full-screen card stack of their photos. Right / tap-❤ = **love**; left = **hide**; down = skip. Ten swipes take ~15 seconds — it's fun, not homework.
- Hide ≠ delete: hidden photos drop out of *their* album view only (no effect on uploader or others' visibility).
- Love is also social signal: a photo loved by ≥ 2 matched subjects gets a public-gallery ranking boost (weak, +10%) — people are good judges of their own photos.

```
people/{personId}/reactions/{mediaId}: {verdict: love|hide, at}
```

Client writes reactions directly (security rules: only own personId); a Firestore-triggered worker folds them into the profile.

## 2. Taste profile — cheap, honest, explainable

Two representations, both stored on the person doc:

1. **Tag-affinity vector (deterministic):** running counters over the curator tags of loved/hidden photos → normalized weights: `{candid: +0.8, posed_group: -0.4, closeup: +0.6, dance: +0.3, …}` plus composition preferences (solo vs group, self-prominent vs contextual). Updated transactionally per reaction; zero LLM cost; fully explainable ("you loved 8 candids, hid 3 posed groups").
2. **Taste memo (LLM, occasional):** after every 15 new reactions (or on personal-reel commission), **Gemma 4** (`gemma-4-26b-a4b-it`, free tier) writes a 3-sentence memo from the affinity vector + exemplar captions: "Aarav prefers candid mid-laughter shots where he's with the groom squad; dislikes posed lineups; loves the dance floor." Stored on the person doc **and written to Agent Runtime Memory Bank** (scoped `{event, personId}` — see §4.1 for the exact key format).

   **Model choice is deliberate, not a bonus-checkbox integration.** This call writes explanatory prose from a pre-computed deterministic vector — no structured-output schema pressure — and sits entirely off the money/critical path: worst case, a malformed memo simply skips that cycle's Memory Bank update, and nothing downstream gates on it. Curator (spec 03 §5.1) already produces the visible photo caption on `gemini-3.5-flash-lite`; giving Gemma a second, redundant caption pass on the same image would be integration theater. Isolating the less battle-tested free-tier model to a genuinely non-critical feature is the same judgment-vs-enforcement discipline as `recompute_visibility` (spec 04 §2), applied to model-selection risk.

**Division of labor (state this in the README):** Firestore is the system of record for product behavior (ranking math reads the vector); **Memory Bank is the *agent's* memory** — directors retrieve memos (and the host's standing preferences like "more candids, less posed") as natural-language context during reasoning. Same pattern at two levels: host prefs steer public output; person memos steer private output.

## 3. Where taste is applied

| Surface | Mechanism |
|---|---|
| Private album ordering | `rank = faceMatchConfidence × quality × (1 + tagAffinity(media))`; hidden filtered out |
| `main_character` personal reel | Commission context includes the taste memo + top-loved photos pinned as "must consider"; the critic rubric gains a check: "does the selection reflect the stated preferences?" |
| Highlights push ("your best shots tonight") | Top-N by the same rank — the album greets returning users with what they'll love |
| Public gallery (weak) | The ≥2-subject-loves boost from §1 |

Cold start (zero swipes): rank by quality × face prominence; the swipe deck's first cards are chosen for *maximum informativeness* (diverse tags), so 10 swipes already separate candid-lovers from posed-lovers.

## 4. Host preference memory (same machinery, event scope)

Host console has one free-text box ("anything the director should know?") + thumbs-up/down on kiosk items and reels. Folded into Memory Bank at `{event, host}` scope; Story Director and Reel Director retrieve it every reasoning step. Demo moment: host types "less posed groups, more candids of the grandparents" → next tick's bounty targets grandparents; next reel version visibly shifts selection — **feedback → adapted behavior, on camera.**

**4.1 Memory Bank scope-key mandate (spec 11 §4).** Every Memory Bank call — this section's host prefs and §2's taste memos alike — uses the literal key format `{eventId}:{personId}` or `{eventId}:host` as the `user_id`/scope string, never a bare `personId`. `personId` is already a per-event ULID (spec 02 §1), so cross-event collision is already improbable; the explicit prefix turns "memory never mixes across events" from an accident of ID randomness into a grep-auditable guarantee. **And: VIP tier is never stored here.** `tier` is deterministic metadata on the person doc (spec 11 §3.1), read by scoring formulas directly — Memory Bank holds only soft, narrative context (taste, free-text preferences) that shapes reasoning, never anything that gates visibility or determines who's "important." Judgment-vs-enforcement, applied to memory itself.

## 5. Acceptance criteria

- [ ] 10 seeded swipes (love candids, hide posed) reorder the private album measurably (rank correlation test) and the memo mentions the pattern.
- [ ] Personal reel for a swiped user contains ≥ 60% loved-tag content vs ~uniform for an unswiped user (fixture comparison).
- [ ] Hide removes a photo from that person's album only — uploader and other subjects unaffected (rules + query test).
- [ ] Host free-text preference visibly changes the next Story Director tick's action mix (trace inspection).
- [ ] Memory Bank entries retrievable across a simulated new session (director cold-restart test).
- [ ] The same real photo/selfie enrolled independently into two seeded test events produces two distinct `personId`s and two distinct Memory Bank entries; updating one's taste memo produces zero measurable change in the other event's ranking (cross-event isolation fixture, spec 11 §4).
- [ ] Grep-level check: every Memory Bank call site's scope key matches `^{eventId}:`.
