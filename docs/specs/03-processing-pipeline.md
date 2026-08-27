# Spec 03 — Processing Pipeline (push-based, per-media state machine)

Goal: every media item, photo or video, flows through perception (curate / faces / safety) within seconds of arrival, at any concurrency, with zero polling, bounded Gemini spend, and replayable failure handling.

## 1. Firestore tree (system of record)

```
events/{eventId}                     # Event Graph: stages[], requiredMoments[], activeStage,
                                     # stageOverride?, status: draft|live|wrapped
                                     # (VIPs are NOT an array here — VIP-ness lives on people/{personId}.tier, spec 11 §3)
  media/{mediaId}                    # see §3 (state machine)
  faces/{faceId}                     # {mediaId, box, embedding(512, unit-norm, vector-indexed), personId?, clusterId?}
  people/{personId}                  # {displayName, uidLinks[], tier: 0-3, consent}
                                     # tier: 0=Principal 1=InnerCircle 2=NamedVIP 3=Guest(default) — spec 11 §3
  people/{personId}/private/{doc}     # anything a guest may not read about another guest (spec 07 taste profile)
  enrollments/{personId}              # {embedding} — the selfie face template, no client rule grants it

  guests/{uid}                       # {points, uploads, personId?}
  bounties/{bountyId}                # spec 05
  reels/{reelId}                     # spec 06
  kiosk/playlist                     # spec 04
  ledger/coverage                    # spec 05 (aggregated coverage counters)
  ops/{alertId}                      # quarantines, DLQ events → host console badge
  claimAudits/{claimId}              # spec 02 §3 — who claimed which faces, how, when (host-visible, reversible)
```

## 2. Push vs pull — the explicit answer

| Hop | Mechanism | Push/Pull |
|---|---|---|
| Bytes arrive | GCS `object.finalized` → **Eventarc** → intake | Push (at-least-once) |
| Intake → perception | **Cloud Tasks** queues (`classify`, `face`, `safety`, `video-prep`) | Push dispatch, **server-side rate-limited** (`max-dispatches-per-second≈8`, `max-concurrent-dispatches≈10` per queue — the Gemini spend throttle) |
| Perception → UI | Workers write Firestore → **snapshot listeners** on every client (gallery, kiosk, bounty banners, album) | Push (~1–2 s end-to-end) |
| Directors | **Cloud Scheduler tick** (Story Director, 2 min) + Firestore-triggered commissions (Reel Director) | Deliberate pull — global gap analysis is a reconciliation loop over aggregate state (Kubernetes-controller pattern), not an event reaction |
| Render completion | Job writes reel doc → listeners | Push |

**No client ever polls.** Two photos arriving "later" are just two more events through the same DAG; ordering is irrelevant because every handler is idempotent per `mediaId` and galleries sort by `capturedAt` (EXIF), not arrival time.

**Burst honesty:** the queue rates are calibrated against Gemini spend-tier caps (math in spec 09 §2). The p50 ≤ 5 s figure holds at ≤ 10 uploads/s sustained; a larger burst drains FIFO at the configured rate while the priority queue keeps bounty validation fast and the kiosk stays fresh — the kiosk needs *fresh* content, not *complete* content.

## 3. Per-media state machine

Not one linear status — parallel stage flags with a derived status (stages complete in any order):

```
media/{mediaId}: {
  uploaderUid, batchId, kind: photo|video, consent: {ring: 0|1|2}, subjectVetoes: [],
  duplicateOf?: mediaId,                    # exact-content dupes (spec 01 §5): skip perception, never public
  exifMissing?: bool,                       # WhatsApp forwards/screenshots — capturedAt falls back to uploadedAt
  status: awaiting_upload → uploaded → processing → indexed | quarantined | abandoned,
  stages: { thumb|video_prep: pending|done|failed,
            curate:  pending|done|failed,
            faces:   pending|done|failed,
            safety:  pending|done|failed },
  attempts: {curate: n, ...},
  stageTimings: {curate: {queuedAt, startedAt, doneAt}, ...},   # stamped by each worker — feeds Flight Deck (spec 10)
  usage: {tokensIn, tokensOut},                                 # summed Gemini usage — feeds cost ticker
  # written by workers:
  curator: {stageId, stagePosterior, momentTags[], aestheticScore, quality{blur,exposure,eyesClosed},
            isHighlight, caption, culturalElements[], peopleCountEstimate},
  guardian: {verdict: public_ok|private_only|host_review, reasons[]},
  faces: [{faceId, box, personId?}],          # denormalized for gallery rendering
  visibility: 'self'|'pool'|'public',         # DERIVED — only recompute_visibility writes it (spec 04)
  capturedAt, uploadedAt, gcsUri, thumbUri, displayUri, posterUri?, proxyUri?
}
```

`status='indexed'` when all stages `done`. Any stage `failed` after max attempts → `quarantined` + `ops/` alert; a replay endpoint re-enqueues just the failed stage. Every worker ends by calling `recompute_visibility` in a transaction (spec 04 §2), so visibility is always consistent with the latest verdicts.

## 4. Photo chain vs video chain

**Photo:** intake renders thumbs inline (Pillow, fast) → tasks: curate + faces + safety in parallel.

**Video (the architecture is video-ready by design — same state machine, one extra prep stage):**
1. `video-prep` task → worker (ffmpeg/ffprobe container, 2 vCPU): duration/codec probe; **poster** (best of 3 sampled frames by sharpness); **keyframes** at 1 fps capped at 12 frames → `derived/.../kf_{n}.webp`; **proxy_720.mp4** (H.264, faststart) for in-app playback. Sets `stages.video_prep=done`, then enqueues curate/faces/safety.
2. `curate`: Gemini on poster + keyframes grid (cheap; ~258 tokens/frame). Full Gemini video understanding via Files API is a P2 upgrade for audio-aware moments ("the vows clip") — flag, don't build Day 1.
3. `faces`: InsightFace on keyframes; faces deduped per video by embedding similarity; matched like photos.
4. `safety`: SafeSearch + dignity rubric on poster + keyframes; verdict applies to the whole clip.
5. Reels: the EDL may reference `{mediaId, inSec, outSec}` sub-clips; the renderer trims the *original* (not proxy) — spec 06.

## 5. Perception agent contracts (ADK agents wrapped as task handlers)

### 5.1 Curator (`gemini-3.5-flash-lite`, structured output)
- Input: `classify_768.webp` (or keyframe grid), event context block (stage list with time windows + required moments + active stage + cultural glossary — the glossary is sourced from `event.eventTypeProfile.culturalGlossary`, spec 11 §2, and is host-reviewed, never inferred).
- Output (Pydantic, temperature-free JSON): `curator` block of §3.
- **Stage fusion rule (deterministic, post-LLM):** `stagePosterior = argmax(visual_score[stage] × temporal_prior[stage])` where `temporal_prior` is 1.0 inside a stage's scheduled window (±30 min ramp), 0.15 outside. EXIF `capturedAt` (not upload time) indexes the prior. **Timezone rule:** EXIF `DateTimeOriginal` carries *no timezone* — it is interpreted in `event.timezone` (a required Event Graph field, e.g. `Asia/Kolkata`); scheduled stage windows are stored UTC and compared through it. **Missing EXIF** (WhatsApp strips it): `exifMissing=true` → the temporal prior is *flattened* (0.5 everywhere) so the visual signal dominates rather than a wrong upload-time prior misleading it. The raw `visual` distribution is stored too — the Story Director consumes disagreement as a stage-drift signal (spec 05 §4).
- Prompt style: rubric-anchored scores (defined 0/0.25/0.5/0.75/1.0 anchors for aesthetics) so scores are comparable across photos; few-shot with 3 annotated examples per event type.

### 5.2 Face Indexer (no LLM)
- InsightFace `buffalo_l` (ONNX CPU, warm in the worker container; ~150–300 ms/photo): detect → 5-pt align → 512-d embed → L2-normalize.
- Match: `findNearest(DOT_PRODUCT)` against enrolled selfie embeddings (`enrollments/{personId}.embedding` — kept out of the person document because Firestore rules grant whole documents and `people/{personId}` must stay readable for display names and VIP tiers), threshold τ_match = 0.45 cosine (calibrate Day 3 on 20 labeled pairs; store distance for tuning).
- No match → assign/attach `clusterId` via incremental threshold clustering (τ_cluster = 0.55) so unclaimed people still group ("Person 7").
- **Split-brain tolerance (concurrency is real here):** with `max-concurrent=8` on the face queue plus vector-index visibility lag, two workers processing the same unclaimed person can each create a cluster — this is *accepted, not prevented* (serializing cluster creation would kill throughput). Two mechanisms make it harmless: (a) **claims are face-level, not cluster-level** — selfie enrollment/re-claim runs `findNearest` over the `faces` embeddings themselves and claims *every* face ≥ τ_claim regardless of `clusterId`, so fragmentation never costs an album a photo; (b) a **cluster-merge reconciliation sweep** (folded into the hourly orphan sweep) compares cluster centroids and merges pairs ≥ τ_cluster in one batched update. Optimistic create, deterministic reconcile.
- Writes `faces/` docs + denormalized `media.faces[]`.
- **Claim-integrity gate (spec 02 §3) — lives in the API enrollment path, not this worker:** a first-time enrollment whose face-level claim would link ≥ `CLAIM_REVIEW_THRESHOLD` (8) faces is held for host review before any `personId` is written to face docs. This worker's outputs (embeddings, clusters) are unaffected; it never writes `personId` on its own for unclaimed people.

### 5.3 Guardian (SafeSearch + dignity rubric)
- Pass 1: Cloud Vision `SAFE_SEARCH_DETECTION` — adult ≥ LIKELY → **`blocked`** (Ring 0 forced, uploader-only, `ops/` alert, appears solely in the host moderation area — egregious content must not sit in the host archive or subjects' albums); racy/violence ≥ LIKELY → `private_only` (hard gates, no LLM override).
- Pass 2: `gemini-3.5-flash-lite` dignity rubric → `public_ok | private_only | host_review` + machine-readable reasons (`eyes_closed`, `mid_bite`, `wardrobe_risk`, `distress_out_of_context`, `unflattering_angle`, **`minor_prominent`** — a child as the main subject routes to `host_review` before Ring 2; hosts know whose kids are whose, we don't). Distinguishes *ritual emotion* (tears at Kanyadaan = `public_ok`, emotional highlight!) from *distress* (guest crying alone = `private_only`) via the stage context in the prompt.
- **Event-declared sensitivity context (spec 11 §2) combines with stage context, never replaces it.** The prompt additionally receives `event.eventTypeProfile.sensitivityProfile` (`pda`/`alcohol`/`attire` dials, host-reviewed at onboarding — never a hardcoded assumption about any culture). **The event-level dial is a ceiling, not a floor:** stage context can only push a verdict *more* conservative than the dial (a solemn-ceremony stage tightens PDA even at a `public_ok`-dialed event), never less conservative than what the host declared; `private_only` always wins outright at any stage. This is the concrete mechanism behind "the same shot type reads `public_ok` at one host's event and `host_review` at another's" — implemented as declared per-event context, not a branch on event type in the prompt template itself.
- `host_review` → host console queue; host decision overwrites verdict (audited).
- **Refusal/schema failure = conservative:** if Gemini refuses or returns unparseable output after 1 retry, verdict defaults to `host_review` — never `public_ok` by accident.

## 6. Failure & replay (judging: "failure handling")

- **Failure taxonomy — transient vs permanent (retrying can't fix a poisoned input):**
  - *Transient* (429, 5xx, timeouts, network): Cloud Tasks retry, max 5 attempts, backoff 10 s→300 s, Gemini retry-after honored.
  - *Permanent* (decode error, schema-invalid after 1 in-handler retry, content refusal, asset deleted): mark the stage `failed_permanent` immediately, apply the stage's conservative default (Curator → aestheticScore 0 + `needs_review`; Guardian → `host_review`), return 200 so Tasks does NOT retry. A poisoned photo costs one pass, never a retry storm.
- Tasks are **unnamed** (see spec 01 §5 — named tasks add latency and block replays); dedupe lives in the handlers' status transactions.
- Worker crash mid-write: all writes are single-doc transactions; re-run overwrites identically (idempotent).
- Eventarc DLQ (spec 01 §5) + `ops/` alerts + `POST /v1/admin/replay/{mediaId}?stage=` for surgical replays.
- Chaos test before demo: kill the classifier service mid-burst → queue absorbs → service restarts → drains; zero lost photos.

## 7. Acceptance criteria

- [ ] Phone photo → kiosk-eligible (`indexed`, visibility computed) in ≤ 5 s p50 / ≤ 15 s p95 under 10 uploads/s.
- [ ] Burst of 100 uploads: Gemini call rate never exceeds queue config; nothing dropped; DLQ empty.
- [ ] Haldi photo uploaded 6 h late classifies as Haldi (EXIF prior test).
- [ ] Same face across 10 photos → one cluster; selfie enrollment claims all 10 retroactively.
- [ ] Concurrent burst of one person's photos through parallel face workers → duplicate clusters may transiently exist, but the hourly sweep converges to exactly one — and enrollment claims **all** of that person's faces even before the sweep runs (face-level claim test).
- [ ] SafeSearch-flagged test image never reaches Ring 2 regardless of consent; dignity rubric routes a crying-alone photo to `private_only` and a mandap-tears photo to `public_ok`.
- [ ] 60 s video: poster in gallery, proxy plays in-app, keyframe faces matched, sub-clip usable by a reel.
