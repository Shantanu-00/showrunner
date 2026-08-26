# Spec 11 — Event Onboarding, Cultural Profiles & VIP Tiering

Goal: every event carries a declared, host-reviewed social and cultural context — what kind of event this is, who its main characters are, and where its sensitivity dials sit — so the perception, direction, and production agents adapt to *this* event instead of applying one global rubric to a wedding, a birthday, and a bachelor party alike. This spec also answers the questions a public, month-long, credit-spending demo link forces onto any production system: how do *I* (the deployment owner) actually create my own dev and demo events without them competing with a real judge, how many events can strangers run at once and for how long, who gets featured more than a random guest and where, and whether one event's memory can ever leak into another's. Extends spec 08 (read that first); consumed by specs 03, 04, 05, 06.

## 1. Concurrent-live-event hard cap (the circuit breaker)

A public, unrestricted URL live for a month (rules-mandated — the Judging Period runs to Oct 1) is an unbounded cost surface: nothing stops a forwarded link, a curious crowd, or an adversarial tester from pushing many events to `live` and running the full Gemini/Vision/Lyria/Veo pipeline against a fixed credit pool, with zero product-side governor. Two distinct risks live here and get two distinct answers: (a) *my own* development and demo events must never compete with a real judge for capacity, and (b) *the public's* events must be cheap to create, cheap to abandon, and self-cleaning — because a month of public exposure guarantees some will be abandoned, and a few will be adversarial.

### 1.1 Event classes — reserved capacity, not a shared pool

```
events/{eventId}.class: 'protected_demo' | 'internal_dev' | 'public'   # server-assigned, NEVER client-settable
```

`POST /v1/events` **always** forces `class: 'public'` server-side regardless of anything the request body claims — accepting a client-supplied class would let anyone escape every guardrail below by simply asserting `protected_demo`. The only way to get anything else: `request.auth.token.platformAdmin === true` (a one-time custom claim minted on the deployment owner's own account via a CLI script at deploy time, never exposed in any product UI or API response). While authenticated with that claim, creating an event defaults to `class: 'internal_dev'`; the owner can additionally pass `intendedClass: 'protected_demo'` — accepted *only* because the claim already verified who's asking, not because the client asserted it.

**This is also the answer to "how do I actually create my dev/test event vs. the judge demo event":** you don't need a special admin UI. Mint the claim once (`scripts/grant_admin.py {your-uid}`), then use the normal `/host` wizard exactly like any host would — every event you create while signed in with that browser/account is automatically `internal_dev`; run the one-time `scripts/seed_judge_event.py` (extends the existing `seed.py`, spec 09 §5) to create the single `protected_demo` event and reseed it nightly. No parallel product surface to build for an audience of one.

| Class | Counted in `platform/liveEventCount`? | TTL / auto-wrap | Cost ceiling | Purge after wrap |
|---|---|---|---|---|
| `protected_demo` (exactly 1 — the judge-mode event) | No | None | None (covered by the platform-wide budget alerts, spec 09 §4) | Never — nightly reseed instead (README-PLAN Part B) |
| `internal_dev` (your own sandbox) | No | 24h, safety-net only | None (dev needs real budget — a reel-pipeline test alone runs ≈$4, spec 06 §6) | Never automatic — you clean up manually |
| `public` (everyone else, unauthenticated) | **Yes** | **60 min** (configurable) | **$3/event** (configurable) | Hard-purged at the next hourly sweep after wrap |

### 1.2 The cap itself (unchanged mechanism, now scoped to `class=='public'`)

```
platform/liveEventCount: { count: int }   # counts class=='public' events only
```

- **Held-slot statuses:** `live`, `paused`, `wrapping` all hold a slot (per spec 08 §2's table, `paused` still runs director ticks and the kiosk full show; `wrapping` runs the final tick and renders). Only `draft` and `wrapped` release/never hold one.
- **Enforcement point:** `POST /v1/events/{eventId}/lifecycle/go-live`. In one transaction: if `event.class == 'public'`, read `platform/liveEventCount.count`; if `>= MAX_CONCURRENT_LIVE_EVENTS` (env-configurable, default **3**), abort with `409 {code:'CAPACITY', message, contactUrl}` and do **not** flip status; else increment the counter and flip `draft→live` atomically. `protected_demo`/`internal_dev` skip the counter entirely — they were never competing for it. Decrement happens at the Wrap sequence's final step (spec 08 §2, status→`wrapped`), in the same transaction as that write. Never enforced client-side only.
- **The headline point, worth stating plainly: a judge never touches this cap at all.** The judge-mode tour (spec 09 §4) lands on the pre-existing, always-live `protected_demo` event — it is never created through the public capacity-limited path, so "3 strangers squat the slots before a judge arrives" simply cannot lock a judge out, regardless of what anyone else does. Everything below this point is defense-in-depth for the platform's *own* cost/hygiene exposure, not a judge-access requirement.
- **UX:** the host console's Go Live button (spec 08 §4 Controls tab) subscribes to a public, non-sensitive read of `platform/liveEventCount.count` to grey out pre-emptively when at cap, but the *hard* gate is always the server transaction above.
- **Contact-the-developer flow:** the `409` response's `contactUrl` opens a pre-filled `mailto:` to the deployment owner. **Production note (state in README):** this is the honest placeholder for a real tiered-capacity/waitlist flow; the mechanism does not change, only the response to hitting it does.

### 1.3 TTL auto-wrap + fast purge for `public`-class events

A capacity cap alone stops runaway *concurrent* spend but not a slot being squatted indefinitely — someone who goes live and never wraps holds their slot forever. Fix: any `public`-class event auto-wraps `PUBLIC_EVENT_MAX_LIVE_MINUTES` (default **60**) after `liveAt`, unconditionally — no activity check needed, because 60 minutes is already generous for a curious visitor and far more than a legitimate one-off poke needs. The hourly `orphan-sweep` (spec 09 §2) is extended to perform this check and, in the same pass, **hard-purge** (Firestore subtree + GCS objects, not the standard 30-day retention of spec 02 §5 — that retention exists for real hosts' guests who deserve a download window, not for anonymous throwaway test events) any `public`-class event that has been `wrapped` since the previous sweep. Worst case, an abandoned or adversarial public event's full lifecycle — from Go Live to gone — is bounded to roughly two sweep cycles, not 30 days, regardless of how many people try this during the month.

### 1.4 Per-event cost ceiling (an independent, spend-based governor)

The TTL bounds *time*; this bounds *spend* within that time — a scripted burst-uploader could otherwise run up real Gemini/Vision/Lyria/Veo cost inside a single 60-minute window. Each event already accumulates `usage.tokensIn/tokensOut` per media doc (spec 03 §3) and a running `costSoFarUsd` (spec 10 §2); extend the same hourly sweep (and, cheaply, each perception worker's post-write check) to auto-pause uploads (403 new signed URLs) and flag for wrap the instant a `public`-class event's aggregated cost exceeds `PUBLIC_EVENT_COST_CEILING_USD` (default **$3** — enough for a legitimate curious visitor to upload a handful of photos and watch the pipeline work, nowhere near enough to force a reel render or a sustained burst). Never applied to `protected_demo` or `internal_dev` — the demo event needs its normal operating budget, which the platform-wide budget alerts already cover.

### 1.5 Admin kill switch

```
platform/publicCreationEnabled: bool   # default true; writable only under platformAdmin claim
```

If the daily 5-minute check (README-PLAN Part B) ever surfaces a genuinely persistent abuse pattern (someone re-creating events faster than the TTL clears them — itself a much higher-effort, clearly-deliberate pattern that would show up immediately in that daily check), flipping this one flag to `false` stops all new `public`-class Go-Live attempts instantly, without touching the running `protected_demo` event judges are actually using. One Firestore write, no redeploy, no downtime for the thing that matters.

## 2. Event Type Profiles — declared cultural & sensitivity context

**Principle, stated plainly because it matters: the host declares their own event's cultural context. The system never infers or assumes a cultural stereotype from an event-type label.** Templates below are time-saving *starting points*, exactly like the timeline-paste-then-review flow in spec 08 §3.2 ("an LLM parse of a WhatsApp itinerary forward is never silently authoritative") — the host reviews every dial during the creation wizard and can flip any of them in either direction. A "Hindu Wedding" template does not mean "Indian weddings are conservative"; it means *this host's* family gets a sensible default to react to, because reacting to a pre-filled table is dramatically lower-friction than filling one from a blank page.

```
events/{eventId}.eventTypeProfile: {
  templateId: 'wedding_generic' | 'wedding_hindu' | 'wedding_christian' | 'wedding_muslim' |
              'bachelor_bachelorette' | 'birthday' | 'graduation' | 'corporate_offsite' | 'custom',
  vipTopology: 'pyramid' | 'flat',                 # feeds §3
  sensitivityProfile: {                            # host-reviewable dials, per content category
    pda:     'public_ok' | 'context_dependent' | 'private_only',   # kissing, embracing, hand-holding
    alcohol: 'public_ok' | 'context_dependent' | 'private_only',
    attire:  'relaxed' | 'standard' | 'conservative',               # tunes Guardian's wardrobe_risk anchor
  },
  culturalGlossary: [string…],                      # seeds Curator vocabulary + Reel Director music briefs
  requiredMomentsTemplate: [{momentId, label, tierWeight}…],   # pre-fills timeline review (spec 08 §3.2)
}
```

**Built-in templates (starting defaults, all host-editable):**

| templateId | vipTopology | pda default | alcohol default | example required moments |
|---|---|---|---|---|
| `wedding_generic` | pyramid | context_dependent | public_ok | vows, ring exchange, first dance, bouquet toss |
| `wedding_hindu` | pyramid | context_dependent | context_dependent | haldi, sangeet, pheras, kanyadaan, vidaai |
| `wedding_christian` | pyramid | public_ok | public_ok | processional, vows, ring exchange, first dance |
| `bachelor_bachelorette` | flat | public_ok | public_ok | (none required — candid density is the only signal) |
| `birthday` | pyramid | public_ok | context_dependent | cake cutting, toast |
| `graduation` | pyramid | public_ok | private_only | stage crossing, family portrait |
| `corporate_offsite` | pyramid | private_only | context_dependent | keynote, team sessions |
| `custom` | host choice | host choice | host choice | host-authored |

**How the dials are consumed (the load-bearing part):**

1. **Guardian dignity rubric (spec 03 §5.3):** `sensitivityProfile` is passed as structured context alongside the existing stage context. **Rule: the event-level dial is a ceiling, never a floor.** `pda: context_dependent` means Guardian may route a kiss to `public_ok` *only* if stage context also supports it (e.g. the first-dance stage), and stage context can always make the verdict *more* conservative than the event dial (a solemn ceremony stage tightens PDA even at a `public_ok`-dialed wedding) but never *less* conservative than what the host declared. This protects the host's stated comfort level from ever being loosened by a stage misclassification. `private_only` always wins outright, at any stage. This is the concrete mechanism behind "a kiss reads as `public_ok` at a Christian wedding's reception and as `host_review` at a wedding whose family dialed PDA to `context_dependent`" — implemented as a host-declared dial per event, never a hardcoded rule about any culture.
2. **Curator (spec 03 §5.1):** `culturalGlossary` seeds the prompt's moment-tag vocabulary (already stubbed there as "cultural glossary" — this spec is what populates it).
3. **Reel Director (spec 06 §2):** `culturalGlossary` feeds the music brief's "cultural refs" field; `sensitivityProfile` constrains which candidate shots a persona lens is even allowed to propose.
4. **Story Director (spec 05):** `requiredMomentsTemplate` pre-fills `event.requiredMoments[]` at wizard time (spec 08 §3.2) — still shown in the editable review table, still host-confirmable/editable before Go Live, same discipline as timeline parsing.
5. **§3 below:** `vipTopology` sets the enrollment wizard's default tiering behavior.

## 3. VIP tiering, topology, and the "feature this person" backdoor

### 3.1 Tier model

```
people/{personId}.tier: 0 | 1 | 2 | 3      # 0 = Principal, 1 = Inner Circle, 2 = Named VIP, 3 = Guest (default)
```

| Tier | Label | Typical size | Set by |
|---|---|---|---|
| 0 | Principal(s) | 1–2 | Host, at VIP enrollment |
| 1 | Inner circle | 5–20 | Host, at VIP enrollment (or bulk-default under `flat` topology) |
| 2 | Named VIP | a handful | Host, ad hoc during the event |
| 3 | Guest | the rest | Default for every self-enrolled or unenrolled person |

### 3.2 Topology answers "how do we classify people in tiers for THIS event" — worked examples

`vipTopology` (from §2) flips the enrollment wizard's *default direction*, because the right default is structurally different for a large formal event than for a small closed one:

- **`pyramid`** (weddings, graduations, corporate events — anyone can attend, but only a few are protagonists): default tier is **3**; the host explicitly *promotes* a small named set to tiers 0–2. Wedding worked example: tier 0 = the couple (1–2 people); tier 1 = immediate family + wedding party (5–15); tier 2 = named extended family or a notable guest; tier 3 = the other hundreds of guests, unpromoted.
- **`flat`** (bachelor/bachelorette parties, small trips, intimate dinners — attendance itself implies closeness; there is no anonymous-guest layer by the nature of the event): default tier is **1**; the wizard offers a bulk "mark everyone as inner circle" action, and the host *demotes* the exception rather than promoting the rule. Bachelor-party worked example: tier 0 = the honoree (1); tier 1 = every other attendee by default (5–15, all close friends); the host can still demote a reluctant plus-one to tier 3 if they want.
- **Corporate offsite (a `pyramid` variant with different semantics):** VIP-ness here means organizational prominence, not emotional closeness — tier 0/1 = leadership and keynote speakers, tier 3 = general attendees. Same mechanism, different meaning; the host declares it either way, exactly as with sensitivity dials.
- **Guest self-enrollment is unaffected by topology.** A guest who selfie-enrolls (spec 02) always lands at tier 3 by default regardless of `vipTopology` — topology only changes the *host-enrolled* default at wizard time, never the self-service path. A flat-topology event's host can still promote a self-enrolled guest to tier 1 afterward.

### 3.3 `vipWeight` — where tier actually changes behavior

```
vipWeight(tier) = { 0: 3.0, 1: 1.8, 2: 1.3, 3: 1.0 }   # calibrate during build against seeded fixtures
```

Deterministic multiplier, injected at four points (each is a small, explicit edit to an existing formula — no new agent, no new judgment call):

1. **Kiosk hero score (spec 04 §4):** `score = aesthetic × recencyDecay × diversityPenalty × stageMatch × vipWeight(personsInFrame)`.
2. **Reel Director candidate selection (spec 06 §3, SELECT step):** diversity-sampling gets a **guaranteed minimum-representation floor** for tier 0/1 people — "≥1 shot per tier-0/1 person where eligible media exists" — not merely a higher sampling probability, because a probabilistic boost can still exclude the groom's mother by bad luck; a floor cannot.
3. **Story Director coverage ledger & gap urgency (spec 05 §1):** the existing "per stage × VIP: {appearanceCount, bestScore}" ledger entry is weighted by `vipWeight` when the REASON step ranks which gap to act on first — a tier-0 gap outranks a tier-3 gap of equal statistical severity.
4. **Bounty point values (spec 05 §3):** `points = basePoints × vipWeight(targetVip)`, clamped back into the existing `[50, 300]` guardrail band after scaling — the guardrail is the ceiling; tier is the reason a bounty for the bride's mother pays more than a generic one.

### 3.4 Featuring a real visitor in the mobile app (the judge-tour case) — and why the kiosk stays untouched

When anyone — a judge on the guided tour, or any curious guest — selfie-enrolls, the private album (spec 04 §3) already personalizes *for them*: their own face-matched photos, taste-ranked. That's not new work, and it already answers "does the app feel like it's about me." The one genuine gap: the *shared* surfaces (public gallery, kiosk) have no reason to treat a fresh enrollee specially, and shouldn't — a kiosk is one display for every viewer in the room; personalizing a shared feed per-viewer is the wrong shape for the problem and is explicitly **not** built here. What's worth doing, and cheap because it reuses `vipWeight` (§3.3) rather than inventing anything: extend the **public gallery's Highlights ordering** (spec 04 §3) from `aestheticScore` alone to `aestheticScore × vipWeight(personsInFrame)` — the same formula already used for the kiosk hero score. This is a *shared* ranking change (every viewer of `/gallery` sees the same order), never a per-viewer personalization, so it stays architecturally honest.

The judge-tour flow (spec 09 §4) can then auto-promote a freshly-enrolled visitor to tier 1 *inside the `protected_demo` event only*, via `event.demoConfig.autoPromoteEnrollees: true` (spec 09 §5's existing `demoConfig` object gains this one flag) — never a general product behavior. **This distinction matters and must hold in code, not just intent:** in any `public`- or real-host-owned event, self-enrollment always lands at tier 3 regardless of anything (§3.2) — letting guests self-promote to VIP would erase the entire point of host-controlled tiering. The auto-promote flag is checked and honored *only* when `event.class == 'protected_demo'`; the enrollment handler must reject/ignore it otherwise, and this is worth its own test (§7). Framed honestly for the README: this is a disclosed demo convenience, not a hidden thumb on the scale — a real host can manually do the identical thing via the "Feature this person" toggle (§3.5) any time.

Complementary, general (not judge-specific) product polish while we're here: a toast in the guest's own "my uploads" view the moment one of their photos flips to `public` visibility ("🎉 your photo just went live on the kiosk!") — a plain Firestore listener on their own upload's `visibility` field, no new mechanism, delightful for every guest including a judge who happens to upload during the tour.

### 3.5 The backdoor — an explicit, audited ranking override, never a visibility override

Host console (spec 08 §4, new **People** tab) gets a **"Feature this person"** toggle per person. Effect: force-inject their best *currently eligible* photo into the next kiosk hero slot and/or guarantee their inclusion in the next reel's candidate set — audited (`featuredBy`, `featuredAt` on the person doc, visible in the host activity log), symmetric to the existing panic "remove from public" override (spec 08 §5) but pointed the other direction.

**The one rule that keeps this honest: the backdoor overrides *rank*, never *visibility*.** It can promote a photo that `recompute_visibility` (spec 04 §2) already marked `public`; it can never surface a `self`- or `pool`-visibility photo, and it is a hard no-op against a Guardian `blocked` verdict. Judgment (who to feature) stays with the host; enforcement (what's ever eligible to be shown) stays exactly where spec 04 §1 already put it — one deterministic function. This is the same "judgment by agents/host, enforcement by policy" principle stated twice now for two different governance questions, which is itself worth saying to an architect judge: the pattern generalizes.

## 4. Memory Bank scoping — VIP is policy, not memory

**Principle:** `tier` is deterministic metadata, written by the host (or defaulted by `vipTopology`) and read by the four deterministic formulas in §3.3. It is never something an LLM or Memory Bank is asked to "remember" and trusted to apply consistently — doing that would let agent memory silently acquire product-critical authority, exactly what the rubric's "state management" / "tools properly isolated and scoped for security" criteria are designed to catch teams *not* doing.

Memory Bank continues to hold only genuinely soft, narrative context, unchanged from spec 07:

- **Per-person taste memos** (spec 07 §2, now Gemma-4-authored) — steer only that same person's own private album/reel ranking.
- **Per-event host free-text preferences** (spec 07 §4) — inform director *reasoning*, never gate an action outright (the guardrails in spec 05 §1 still apply to whatever the director proposes).

Neither ever encodes VIP tier, and neither ever writes `visibility`.

**Scope-key mandate (grep-auditable, not just documented):** every Memory Bank call uses the literal key format **`{eventId}:{personId}`** (persons) or **`{eventId}:host`** (host prefs) as the `user_id`/scope string passed to `VertexAiMemoryBankService` — even though `personId` is already a per-event ULID (spec 02 §1: "Per event" lifetime), so cross-event collision is already astronomically improbable by construction. The explicit prefix turns "we don't mix memory across events" from an accident of ID randomness into an auditable guarantee: a reviewer (or a judge reading the code) can grep every Memory Bank call site and confirm the key always starts with `eventId:`.

**Explicit non-goal, stated for judges (a privacy/trust guarantee, not a missing feature):** Showrunner never links person identity across events. A guest who appears at two different weddings on the same deployment gets two independent person docs, two independent taste profiles, two independent Memory Bank scopes, with zero data sharing between them. This is deletion-consistent with spec 02 §5 by construction — deleting a person's data in event A can never be observed to affect event B, because there is no shared key to leak through.

## 5. Why this belongs on camera, not just in the backend

The onboarding wizard — picking a template ("Hindu Wedding" vs. "Bachelor Party"), watching the sensitivity dials and required-moments table pre-fill, then tiering the VIPs — is a legitimate second demo beat, not just setup plumbing. It visibly proves three things a judge cannot get from a README paragraph: the sensitivity rubric is **policy-driven configuration**, not a hardcoded per-culture branch in code (an architect judge will check this — §2's "host declares, system never assumes" line is the one to say out loud); the "host reviews before the system trusts it" discipline, already established for timeline parsing, extends consistently to cultural context; and the punchline — *the same agent fleet behaves correctly and differently at a bachelor party versus a wedding, because the host told it to, not because it was special-cased.* Candidate P1 B-roll beat for `docs/video/PLAN.md` if the 4-minute cut has room; the bounty loop remains the spine and is never displaced by this.

## 6. API surface added by this spec

```
POST /v1/events/{eventId}/lifecycle/go-live      # the capacity-gated transition, §1
POST /v1/events/{eventId}/profile                # set/update eventTypeProfile, draft-only, §2
POST /v1/events/{eventId}/people/{personId}/tier # host-authed, set VIP tier, §3.1
POST /v1/events/{eventId}/people/{personId}/feature   # the audited backdoor toggle, §3.5
```

## 7. Acceptance criteria

- [ ] A 4th simultaneous Go Live attempt (with 3 already live/paused/wrapping) is rejected with the capacity message; the host console badge greys out proactively but the server transaction is what actually blocks it (verified by attempting the call directly, bypassing the UI).
- [ ] Wrapping an event frees its slot within the same transaction that flips status to `wrapped`; a 4th event can go live immediately after.
- [ ] Selecting the `bachelor_bachelorette` template auto-defaults every host-enrolled person to tier 1 without individual promotion; selecting `wedding_hindu` defaults every host-enrolled person to tier 3 until explicitly promoted.
- [ ] A kiss photo at the same wedding reads `public_ok` when captured during a stage tagged for celebration and the event's `pda` dial is `public_ok`, but the identical shot type routes to `host_review` when the host dialed `pda: context_dependent` for their event — same code path, different declared context, no culture-specific branch in the Guardian prompt template itself.
- [ ] Two reels generated for the same event, one before and one after a person is enrolled as tier 0, visibly differ in that person's shot count (diff test on storyboard JSON, same style as spec 06 §8).
- [ ] "Feature this person" promotes an already-`public`-visibility photo into the next hero slot within one recompute cycle; the same toggle on a person whose only eligible photo is `pool`-visibility or Guardian-`blocked` is a no-op (rank override never breaches the visibility gate).
- [ ] Fixture test: the same real photo/selfie enrolled independently into two seeded test events produces two distinct `personId`s, two distinct Memory Bank entries; updating one's taste memo produces zero measurable change in the other event's ranking or reel selection.
- [ ] Grep-level check: every Memory Bank call site's scope key matches `^{eventId}:`.
- [ ] A `POST /v1/events` body containing `class` or `intendedClass` from an unauthenticated (or non-admin) caller is silently ignored — the created event is always `class: 'public'`; only a `platformAdmin`-claimed caller can obtain `internal_dev`/`protected_demo`.
- [ ] A `public`-class event that never wraps auto-wraps at `PUBLIC_EVENT_MAX_LIVE_MINUTES` and is hard-purged (Firestore + GCS both empty) within the next sweep cycle; an `internal_dev` event past its 24h safety-net TTL auto-wraps but is **not** purged.
- [ ] A `public`-class event whose aggregated cost crosses `PUBLIC_EVENT_COST_CEILING_USD` has uploads paused within one sweep cycle, regardless of how much of its 60-minute TTL remains; a `protected_demo`/`internal_dev` event never hits this check.
- [ ] Flipping `platform/publicCreationEnabled` to `false` rejects new `public`-class Go-Live attempts immediately while the running `protected_demo` event is completely unaffected.
- [ ] `autoPromoteEnrollees` only fires when `event.class == 'protected_demo'`; the identical selfie-enrollment call against a `public`-class or real host-owned event leaves the new person at tier 3 (fixture test, both branches).
- [ ] Public gallery Highlights ordering measurably reflects `vipWeight` (a tier-0/1 person's photo of equal aesthetic score outranks a tier-3 photo) — and this ordering is identical for every viewer (no per-viewer personalization exists anywhere in `/gallery`).
