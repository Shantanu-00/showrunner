# Spec 09 — Infrastructure, Configuration & Demo Operations

Goal: every deployable unit with its exact scaling/config values, the Firestore index/rules inventory, and the demo-day runbook. A coding agent should never have to invent an infrastructure number — they're all here.

## 1. Service inventory (all Cloud Run unless noted, region `us-central1`)

| Unit | Kind | CPU/Mem | Scaling | Concurrency | Notes |
|---|---|---|---|---|---|
| `api` | service | 1 / 512Mi | 0→10 (**min 1 during event/demo**) | 80 | signed URLs, claims, consent, internal/tick |
| `intake` | service | 2 / 2Gi | 0→20 | 10 | Eventarc target; Pillow + pillow-heif baked in |
| `worker-curate` | service | 1 / 1Gi | 0→10 | 8 | Tasks target; Gemini calls |
| `worker-face` | service | 2 / 4Gi | 0→5 (**min 1 during event/demo** — 326 MB model, cold start ≈ 30 s+) | 4 | InsightFace ONNX baked into image at build, loaded once at startup |
| `worker-safety` | service | 1 / 1Gi | 0→10 | 8 | Vision + Gemini |
| `worker-video-prep` | service | 2 / 4Gi | 0→5 | 2 | ffmpeg/ffprobe |
| `publisher` | service | 1 / 512Mi | **min 1 / max 5** | n/a | Firestore listener; per-event lease (`publisherLease/{eventId}`) — single writer per event, not a global singleton (spec 04 §4) |
| `render` | **job** | 8 / 32Gi | task per commission | 1 | ffmpeg + librosa(<1.0) + fonts |
| `story-director`, `reel-director` | **Agent Runtime** | defaults (4 vCPU/4Gi) | min 0 | 9 | auto Identity/Registry/Observability; OTel env vars per PLAN §5 |
| kiosk/PWA frontend | Firebase Hosting (static export) + `api` | — | — | — | App Hosting optional if SSR needed |

## 2. Queues, triggers, schedule

```
Cloud Tasks (all: unnamed tasks, retry max 5, backoff 10s→300s):
  classify-queue:    max-dispatches-per-second=8,  max-concurrent=10
  face-queue:        max-dispatches-per-second=10, max-concurrent=8
  safety-queue:      max-dispatches-per-second=8,  max-concurrent=10
  video-prep-queue:  max-dispatches-per-second=2,  max-concurrent=2
  priority-queue:    max-dispatches-per-second=20, max-concurrent=10   # bounty submissions
  renders-queue:     max-dispatches-per-second=1,  max-concurrent=2

Eventarc: storage.object.v1.finalized on {raw} → intake
  underlying subscription: DLQ topic `eventarc-dlq` (max 5 delivery attempts)
  → dlq-consumer (tiny service): mark quarantined + ops/ alert

Cloud Scheduler: `director-tick`      — every 2 min  → POST {api}/internal/tick (OIDC service account)
                 `director-tick-demo` — `* * * * *`  → POST {api}/internal/tick?demo (OIDC), scoped to
                                        `class=='protected_demo'` events only; paused by scale-down.sh.
                                        Each invocation ALSO enqueues one Cloud Task on
                                        `priority-queue` with schedule_time = now + 30 s hitting the
                                        same endpoint ⇒ **effective demo cadence 30 s** (Scheduler's
                                        cron floor is 1 min). Scheduler is the heartbeat and the
                                        watchdog: a dropped interleave self-heals on the next minute,
                                        and the tick lease (spec 05 §1) makes a double-fire a no-op.
                 `orphan-sweep` — hourly → POST {api}/internal/sweep. Four jobs in one pass:
                                  orphaned uploads · cluster-merge reconciliation (spec 03 §5.2) ·
                                  public-class TTL auto-wrap + hard-purge of events wrapped since the
                                  previous sweep (spec 11 §1.3) · public-class cost-ceiling check,
                                  pause+flag-for-wrap on breach (spec 11 §1.4)
```

### Queue-rate calibration (why 8/s, and the honest SLA)

The Gemini-calling rates are pinned to the spend tier, not guessed. Classify and safety each call `gemini-3.5-flash-lite` at ~1,548 tokens in + ~300 out ≈ $0.0012/photo; 8/s × 600 s ≈ $5.8 per 10 minutes *per queue*, so the two Gemini queues together sit at ≈ the Tier-1 $10/10-min rolling cap. Consequences (state them in the README):

- The rates are **configuration, not architecture** — a Tier-2 upgrade raises them with one `gcloud tasks queues update`.
- The Batch API (50% off) is the designated backfill lane for burst tails.
- The honest SLA: p50 ≤ 5 s kiosk-eligible at ≤ 10 uploads/s sustained. A larger burst drains FIFO at the configured rate while the priority queue keeps bounty validation fast and the kiosk stays fresh — the show needs *fresh* content, not *complete* content, so it never stalls even while the tail waits.

## 3. Firestore inventory

- **Vector index:** `events/*/faces embedding` — 512 dims, COSINE (unit-norm ⇒ equivalent to dot product), created via gcloud CLI.
- **Composite indexes:** media (`visibility asc, status asc, capturedAt desc`), (`visibility, status, uploadedAt desc` — kiosk `just_in` strip orders by *upload* recency, spec 04 §4), (`visibility, curator.isHighlight, curator.aestheticScore desc`), (`albumOf array, capturedAt desc`), bounties (`status, expiresAt`), reels (`persona, version desc`).
- **Exemption:** `media.createdAt` single-field index **disabled** (sequential-timestamp 500 writes/s cap); ordering uses `capturedAt` via composites above.
- **TTL:** event subtree retention per spec 02 §5.
- **Security rules (skeleton — identity via custom claims only):**

```
match /events/{e}/media/{m} {
  allow read: if isHost(e)
    || resource.data.uploaderUid == request.auth.uid
    || (resource.data.visibility == 'public' && resource.data.status == 'indexed')
    || (resource.data.visibility == 'pool'
        && request.auth.token.personId in resource.data.albumOf);
  allow write: false;   // media mutations go through the API/workers only
}
match /events/{e}/people/{p}/reactions/{r} {
  allow write: if request.auth.token.personId == p
               && request.resource.data.keys().hasOnly(['verdict','at'])
               && request.resource.data.verdict in ['love','hide'];
}
match /events/{e}/bounties/{b}   { allow read: if isEventMember(e); }
match /events/{e}/kiosk/playlist { allow read: if true; }
function isHost(e) { return request.auth.token.host == e; }
```

Rules get a **unit-test suite** (Firestore emulator) — spec 04 acceptance depends on it.

## 4. Secrets, budget, teardown

- Secrets (Secret Manager, mounted): Gemini API key (billed tier only), VAPID keys. Everything else = ambient service-account auth; one least-privilege SA per service (intake can't call Gemini; curate can't touch GCS raw writes; render is the only curated-bucket writer).
- Budget alert at $50/$100/$140. **Post-submission posture: scale down, don't tear down** — the rules require the project to remain available for judge testing until Oct 1 (judging period), and a hosted URL is "highly encouraged". `deploy/scale-down.sh`: all services to min-instances=0 (cold starts acceptable for judges), scheduler paused except a slow demo-event tick, one seeded **judge-mode event** kept live. Idle cost ≈ a few $/month. `deploy/teardown.sh` runs after winners are announced (Oct 8+).
- **Concurrent-live-event hard cap (spec 11 §1) — the platform's circuit breaker.** A public, unrestricted demo URL live for a month (rules-mandated, Judging Period ends Oct 1) is an unbounded cost surface: nothing stops a forwarded link or an adversarial tester from spinning up "live" events and running the full Gemini/Vision/Lyria/Veo pipeline against $150–$300 of credits with zero product-side governor. `MAX_CONCURRENT_LIVE_EVENTS=3` (env-configurable) is enforced transactionally at Go Live against `platform/liveEventCount` — but **only for `class=='public'` events**; the judge-mode event and the deployment owner's own dev events are a different class entirely and never compete for these slots (spec 11 §1.1). `public`-class events additionally auto-wrap after 60 min and hard-purge on the next sweep, and auto-pause past a $3 cost ceiling — so even unlimited strangers trying this can't hold a slot indefinitely or run up real spend. This is a real production-readiness signal, not hackathon theater.
- **Judge mode:** the hosted URL lands on a guided page — "You're a judge? 60-second tour:" pre-populated `protected_demo` event (spec 11 §1.1 — exempt from every guardrail above, since it's the one thing that must always work), one-tap guest join with 3 sample photos ready to upload, kiosk link, Flight Deck in presentation mode, and testing credentials repeated from the submission instructions. **The tour explicitly narrates the share toggle** — *"tap 'Share to the big screen' — everything is private-by-default"* — because consent defaults to the pool ring (spec 02 §4): without that step a judge's upload never reaches the kiosk and reads as breakage; with it, a potential failure mode becomes a consent-architecture demo moment. Judges get the wow-loop in under a minute with zero setup, and — because they're never on the public capacity-limited path — zero exposure to anything the cap/TTL/kill-switch above is doing to deter everyone else.

## 5. Demo mode (design once, demo twice)

Real weddings span days; a demo has 4 minutes. `event.demoConfig` (host console, draft state only):

- **Compressed timeline:** stage windows minutes long instead of hours. All temporal logic already flows through the Event Graph, so nothing special-cases.
- **`autoPromoteEnrollees: bool`** (spec 11 §3.4) — when true, a fresh selfie-enrollment auto-tiers the enrollee to 1 (Inner Circle) so the judge-tour visitor sees `vipWeight` effects live. The enrollment handler honors this flag **only** when `event.class == 'protected_demo'`; it is a no-op on every other event regardless of the flag's value, so this can never become a way for a real guest to self-promote.
- **`publicFloor: 0.0`** (protected_demo only; real events default 0.45, spec 04 §2) — a judge's test photo (their desk, their badge) would fail the aesthetic floor, never turn `public`, and read as "it's broken." With the floor at 0 in the demo event, consent + Guardian alone decide `public`, so any consented safe upload hits the kiosk `just_in` strip < 5 s; quality still governs hero curation via the aesthetic score term. Disclosed in the README next to `autoPromoteEnrollees` — a demo convenience, not a hidden thumb.
- **Seeded dataset:** 40–50 curated photos with **synthetic EXIF `DateTimeOriginal` values aligned to the compressed windows** (the temporal prior must fire correctly on stage) + 2 short videos + 3 pre-enrolled people. A `seed.py` script uploads them through the real pipeline (never direct Firestore writes — judges may check); its judge-mode variant (`scripts/seed_judge_event.py`, spec 11 §1.1) is what actually assigns `class: 'protected_demo'`, since that class can never come from the public API.
- **Director cadence: 30 s in demo mode, and it must be server-side.** The cadence is delivered by the `director-tick-demo` Cloud Scheduler job plus its +30 s Cloud Task interleave (§2); production `director-tick` stays at 2 min. **A console-driven `/internal/tick?demo` loop is explicitly rejected**: on camera it is indistinguishable from pressing a button, and the highest-weighted thing this project has to prove is *"intercept and complete a multi-step background workflow without human intervention."* The loop and spec 05 §1's **Run director now** button both remain in the codebase as dead-air fallbacks, but neither is on the demo's happy path, and the Cloud Scheduler job-detail page (`Schedule: * * * * *` · `Last run: 18 seconds ago` · `Result: Success`) is the autonomy evidence. **Truthfulness constraint for the video:** the console shows a 1-minute cron, so never say "every thirty seconds" over that page without naming the interleave in the same breath.
- **Warm-up runbook (execute 10 min before recording):** `deploy/up.sh` → min-instances take effect → upload 1 throwaway photo (warms intake/curate/face paths + confirms E2E) → kiosk "Start show" tap (audio unlock + wake lock) → verify Observability tab shows traces → phones on venue Wi-Fi + hotspot fallback → OBS/screen-record the GCP console tabs (Cloud Run, Firestore, Traces) *before* the acted demo.
- **Chaos rehearsal (once, Day 6):** kill `worker-curate` mid-burst → verify queue drains on restart with zero loss; replay a DLQ'd item from the console.

## 6. Acceptance criteria

- [ ] `deploy/up.sh` from a clean project → all services healthy + one E2E photo flows in < 15 min (README spin-up proof).
- [ ] Rules emulator suite green (stranger/pool/subject/host/banned matrix from specs 02/04/08).
- [ ] Demo-mode seeded run: QR-scan phone upload → kiosk < 5 s; director tick issues the scripted bounty on the compressed timeline; reel renders and premieres — full 4-minute arc rehearsed end-to-end twice.
- [ ] `teardown.sh` → billing shows ~$0/day idle; `up.sh` restores in < 15 min.
