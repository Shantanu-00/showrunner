# Spec 10 — Flight Deck (live pipeline visualizer)

Goal: the demo must *show* the GCP pipeline running — not narrate over console-tab cuts. The Flight Deck is a host-console page that renders the architecture diagram **with real traffic animating through it**, driven entirely by data the pipeline already writes. It is simultaneously: the demo centerpiece, evidence for the Architectural Discipline 30% + Best Architectural Design prize, and a genuinely useful ops view.

## 1. Design principle

**Truthful by construction.** The Flight Deck invents no telemetry — it renders the same Firestore documents the pipeline mutates (`stages`, `stageTimings`, `usage`, queue-state aggregates, director session events). If the Flight Deck shows a photo flowing, the photo actually flowed. This also makes it demo-safe: no console logins, no IAM friction, no screen-recording of laggy cloud UIs.

## 2. Data contract additions (written by existing workers — small, do in spec 03 code)

```
media/{mediaId}.stageTimings: {                    # each worker stamps its own stage
  intake:  {at},                                    # finalize→doc flip
  curate:  {queuedAt, startedAt, doneAt},
  faces:   {queuedAt, startedAt, doneAt},
  safety:  {queuedAt, startedAt, doneAt}
}
media/{mediaId}.usage: {tokensIn, tokensOut}        # summed Gemini usage for this item
ops/pulse_shards/{workerType}: one doc per worker type (intake, curate, face, safety, video_prep),
  written ONLY by workers of that type, throttled ≤1 write/s per shard:
  {itemsPerMin, queueDepth,                         # queue depth = count of that stage 'pending' (aggregation query, cached 5 s)
   p50Ms, p95Ms, tokensIn, tokensOut, costSoFarUsd}
  The Flight Deck client merges shards in memory for the header stats (photos/min, total cost;
  activeGuests via a guests aggregation query). True shards, not a shared doc: Firestore's
  sustained-write guidance is 1/s per document — one "pulse" doc fed by five worker types × N
  instances is a hot-doc anti-pattern.
```

Directors already write session/action events (spec 05); the Flight Deck tails the latest tick's `assessment` + `actions`.

## 3. The page (`/host/flightdeck`, host-authed; read-only "presentation mode" toggle for filming)

Layout mirrors `docs/architecture.md` §2 — three bands + governance rail, each node carrying its **GCP service name + logo**:

```
[ Phone ] → [ Cloud Storage ] → [ Eventarc ] → [ Cloud Run · intake ] → [ Cloud Tasks ]
                                                            ├─ [ Cloud Run · Curator — Gemini 3.5 Flash-Lite ]
                                                            ├─ [ Cloud Run · Faces — InsightFace + vector search ]
                                                            └─ [ Cloud Run · Guardian — Vision + Gemini ]
                                                    → [ Firestore ] → [ Kiosk | Gallery | Albums ]
   CONTROL RAIL: [ Cloud Scheduler ] → [ Agent Runtime · Story Director ] → bounty feed
                 [ Agent Runtime · Reel Director ] → [ Lyria 3 ] → [ Cloud Run Job · ffmpeg ] → premiere
```

Live behaviors:
- **Chip animation:** each media doc in a non-terminal state renders as a small thumbnail chip positioned at its current stage; Firestore snapshot deltas move it along the edges (CSS transitions, ~600 ms). Completed chips fly off into the "surfaces" node. Failures pulse red at their stage and drop into a DLQ tray (with replay button — ops!).
- **Stage node meters:** each node shows live p50/p95 latency (from `stageTimings`), items in flight, and for LLM nodes a **token/cost ticker** ("38 photos · 61k tokens · $0.04").
- **Queue arcs:** Cloud Tasks edges thicken with queue depth — a burst upload visibly "fills the pipe" and drains at the configured 8/s, which *demonstrates the rate-limiter working* (say it in the VO: "watch the queue absorb the burst — that's Cloud Tasks metering our Gemini spend").
- **Director panel:** right rail streams Story Director ticks as cards — assessment sentence, actions taken, guardrail rejections — and Reel Director commissions with render progress bars. A bounty card visibly travels: director → Firestore → (cut to phone) banner.
- **Header stats:** photos/min, active guests, total cost so far, event stage.

## 4. Demo choreography (replaces console-tab roulette; PLAN §13 segment 3)

1. Camera on Flight Deck full-screen. Second phone uploads 10 photos on camera.
2. Ten chips appear at Cloud Storage, fan through the three worker lanes at the throttled rate, land in Firestore; kiosk (picture-in-picture) pops the best one seconds later. One unbroken shot = the whole architecture, working.
3. "Run director now" → tick card appears → bounty chip flies → phone banner pops (same shot).
4. **Corroboration cut, 15 s** (rules require visible Google Cloud): Cloud Run service list, one Agent Observability trace DAG expanded, Firestore console mutating. The Flight Deck is the story; the console is the receipt.

## 5. Scope guardrails

- Build cost: one React page + Firestore listeners + the §2 field stamps ≈ half a day (Day 6). No new services, no new infra.
- P0 within this spec: chip flow + node meters + director feed. P1: queue-depth arcs, DLQ tray, presentation mode polish. Cuttable entirely if Day 6 explodes — but it directly feeds two prize criteria, so cut last among P1s.
- Not a public surface: host-authed only (it exposes uploader activity and ops data).

## 6. Acceptance criteria

- [ ] Upload 10 photos → 10 chips traverse all stages with truthful timing (spot-check one chip's timings against its Firestore doc).
- [ ] Kill worker-curate mid-flow → chips stack at the queue edge, red pulse on recovery-retry, drain on restart — the chaos test is *visible*.
- [ ] Token/cost ticker within 5% of the billing-page math for the session.
- [ ] Director tick card appears ≤ 2 s after "Run director now"; bounty banner on a second device in the same take.
- [ ] 4-minute recording rehearsed twice entirely from Flight Deck + phone + kiosk PiP + one 15 s console cut.
