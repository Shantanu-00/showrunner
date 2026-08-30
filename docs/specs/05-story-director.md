# Spec 05 — Story Director (event monitoring, coverage, bounties)

Goal: the autonomous heart of the Taskmaster pitch. Nobody asks it anything. Every 2 minutes it reconciles *what the timeline says should be happening* with *what the photo stream proves is happening*, and acts: bounties, escalations, stage transitions, reel commissions.

## 1. Who monitors the event — the control loop

Deployed on **Agent Runtime** (auto Agent Identity, Registry, Observability traces). Trigger: **one global Cloud Scheduler job** (every 2 min, OIDC) → `POST /internal/tick` on the API service → queries `events where status in ('live','wrapping')` → for each, attempts a **tick lease** (transactional `ticks/{eventId}` doc: acquire only if unheld or expired; lease TTL 5 min) → invokes that event's director `:query`. The lease is what prevents overlapping ticks (a slow tick + a 2-min schedule would otherwise run two directors concurrently and double-issue bounties); it is **released when the tick completes**, so the TTL is a crash backstop and not the cadence — a lease held for its full TTL would throttle the schedule rather than protect it. `wrapping` is ticked as well as `live` because the wrap-up report and the finale are the last things the director does (spec 08 §2), and an event that stopped being ticked the moment the host pressed "wrap" would never produce them. Scheduler is *not* per-event — events going live/wrapped need no infra changes, only a status flip (spec 08).

`/internal/tick` is on the one service deployed `--allow-unauthenticated` (spec 09 §1: guests' phones carry Firebase ID tokens, and Cloud Run's IAM check is per-service, not per-path), so it **verifies its caller in the handler**: a Google-signed OIDC token whose `email` is on the deployed service-account allowlist and whose `aud` names this service, or a Firebase host/`platformAdmin` token for the fallback button below (scoped to one event the caller actually hosts). Every tick also writes one heartbeat document, `platform/tickPulse`, whether or not it found work — autonomy that exists only in Cloud Logging cannot be asserted by a test or read by the judge page's next-tick countdown.

The host console also has a **"Run director now"** button (same endpoint, host-authed, bypasses the wait but not the lease) — a **dead-air fallback only, never the demo's happy path.** Pressing it seconds before claiming the system runs without human intervention forfeits the thing that matters most, so the demo runs on the real `director-tick-demo` Cloud Scheduler job at a 30 s effective cadence (spec 09 §2/§5) and the button exists for the case where a tick misses its window on camera.

Each tick (a `SequentialAgent`: Ledger → Reason → Act):

```
1. LEDGER (deterministic, no LLM): aggregate Firestore into ledger/coverage:
     per stage × requiredMoment: {photoCount, bestAestheticScore, lastCapturedAt}
     per stage × VIP:            {appearanceCount, bestScore, tier}   # tier from people/{personId}, spec 11 §3
     stage-drift signals: visual-vs-temporal disagreement rates from recent curator outputs
     bounty states, upload velocity (5-min window), active guest count
2. REASON (gemini-3.7-flash, structured output): input = Event Graph + ledger + its own
     session state (what it did last ticks) + Memory Bank (host preferences).
     Output: {assessment, actions[]} where action ∈
       ISSUE_BOUNTY {targetMoment, targetVip?, title, guestFacingCopy, points, expiresInMin, audience}
       ESCALATE_BOUNTY {bountyId}          # wider audience / more points / kiosk takeover
       PROPOSE_STAGE_ADVANCE {toStageId, confidence, evidence}
       COMMISSION_REEL {persona, stageId?} # e.g. stage ended + coverage rich → recap reel
       ANNOUNCE {kioskMessage}             # "Pheras beginning at the mandap ✨"
       NO_OP {reason}
3. ACT (deterministic executors): validate & apply actions with GUARDRAILS:
     ≤ 2 new bounties/tick, ≤ 6 active total; no duplicate bounty per (moment, vip);
     points = clamp(basePoints × vipWeight(targetVip), 50, 300) — the guardrail band is the
     ceiling, tier (spec 11 §3.3) is the reason a bride's-mother bounty outpays a generic one;
     stage advance auto-applies only if confidence ≥ 0.8 AND (the scheduled window agrees, OR
     the drift signal has named that same stage for DRIFT_ADVANCE_TICKS consecutive ticks —
     spec 13's evidence leg); else it becomes a host-console suggestion card. The window is
     max(45 min, 0.25 × minutes to the nearest neighbouring stage) — a wedding scheduled in
     30-minute beats keeps the literal ±45, a trip in 4-hour blocks gets a window sized to
     its own grain (revised with spec 13; previously a flat ±45).
```

**Tier also orders which gap gets acted on, not just how it's paid.** When the REASON step has multiple candidate gaps of similar statistical severity, it ranks by `vipWeight(targetVip)` first — a tier-0 gap (the couple) outranks a tier-3 gap (a random guest cluster) of equal photoCount deficit. Tier is deterministic metadata read at reasoning time, never something the LLM is trusted to infer or remember on its own (spec 11 §4).

Session state (Agent Runtime Sessions) carries tick-to-tick memory: issued bounties, deferred ideas, last assessment — so the LLM reasons over a narrative, not a cold start. Context stays bounded regardless of event length: the session keeps a **rolling window of the last 10 tick summaries**, with older ticks compacted into a single paragraph (compaction happens in the deterministic Act step, not by the LLM). **Every tick's reasoning is visible in the Observability trace DAG — this goes in the demo video.**

## 2. Stage tracking — timeline as prior, not truth

Weddings run late. Three inputs, fused:
- **Schedule** (Event Graph windows) = prior.
- **Visual evidence** = Curator's per-photo raw stage distributions; a run of high-confidence off-schedule classifications (e.g. 12 of last 20 photos look like Pheras during the Sangeet window) is the drift signal computed in the ledger step.
- **Host override** = the host console's big "Now: ▶ Pheras" button (and MC shortcut) always wins instantly.

`PROPOSE_STAGE_ADVANCE` applies the guardrail above. The drift signal's *streak* (how many consecutive ticks it has named the same target, persisted on the director state) is what licenses an advance the schedule disagrees with: one tick's drift can be a burst of forwarded photos from this morning, two consecutive ticks against fresh uploads is a place (spec 13). The host's override still beats any amount of evidence, instantly. Stage change fans out: kiosk re-theme, **immediate arming of the new stage's required-moment bounty templates**, `ANNOUNCE` slot, temporal prior update for the Curator context block.

**Gap lifecycle (spec 13):** a stage whose `endsAt` is more than `STAGE_GAP_GRACE_MINUTES` past emits no live gaps — nobody can photograph it any more, and on a multi-day event Day 1's misses must not crowd Day 4's live gaps out of the prompt and the bounty budget. Its uncovered moments are archived exactly once into `directorState.permanentGaps` (beside the expired-bounty records), which is what the wrap report reads. **Idle ticks (spec 13):** when nothing is scheduled within `TICK_IDLE_LOOKAHEAD_MINUTES` (nor within the grace window behind), nobody has uploaded, no bounty is open and the host holds no override, the tick runs only its deterministic steps (Validate/Expire — awards never wait) and skips the REASON model call entirely, reporting `mode: "idle"` and leaving no session line. A 5-day trip is ~3,600 ticks; without this every overnight tick is a paid model call.

**Two coverage mechanisms, deliberately split:** *scheduled arming* for predictable moments — a varmala or bouquet toss lasts under a minute, and reactive detection can only notice the absence after it's over, so those bounties go live from the timeline prior the second their stage begins; *reconciliation* (the 2-min tick) for statistical gaps that only aggregate evidence reveals ("40 minutes into the Haldi, zero photos of the grandmother"). Anticipate the predictable, reconcile the statistical.

## 3. Bounty lifecycle

```
bounties/{bountyId}: {status: active → (claimed*) → fulfilled | expired,
  targetStage, targetMoment, targetVip?, title, copy, points, expiresAt,
  audience: all | nearStage | topContributors, submissions: [{mediaId, uid, verdict, score}]}
```

- **Delivery:** guests' PWAs listen on `bounties` where `status=='active'` → animated mission banner. (Primary channel = Firestore listener — works in every browser including iOS Safari; FCM web push fires additionally where available. Never demo-critical.) Kiosk shows `bounty_call` slots on escalation.
- **Submission:** guest taps banner → camera → upload flows the *normal* pipeline (spec 01/03) with `bountyId` stamped at intent time; intake routes it to a **priority Cloud Tasks queue** (same worker, faster dispatch) so validation feels instant.
- **Validation:** after curate completes, a bounty-check step (flash-lite, structured) scores the photo against the bounty's machine-readable criteria (`targetMoment`, `targetVip` via face match — the *identity* check comes from Face Indexer, not the LLM). `fulfilled` → transaction: award points to `guests/{uid}`, mark bounty, leaderboard listener updates everywhere, kiosk celebration slot. Partial credit (right moment, weak quality) → smaller award, bounty stays open.
- **Expiry/escalation:** unfilled critical bounty past half-life → `ESCALATE_BOUNTY` (more points, kiosk takeover). Expired → recorded in ledger as a permanent coverage gap (the wrap-up report tells the host honestly).

## 4. Anti-spam & fairness

Max 1 banner per guest per 10 min (client-side gate on the listener); bounty audience targeting (`nearStage` = guests who uploaded in the last 15 min); per-uid daily points cap; duplicate submissions to one bounty keep only the best score.

## 5. Acceptance criteria

- [ ] Seeded scenario "Pheras active, zero bride's-mother photos" → within one tick a correctly-worded bounty exists and banners appear on connected clients (no human input anywhere).
- [ ] A submitted photo matching the moment+VIP fulfills the bounty and awards points transactionally (no double-award under concurrent submissions).
- [ ] Stage-drift scenario (Sangeet window, Pheras-looking photos) → proposal card appears on host console; confidence ≥ 0.8 + window agreement → auto-advance; confidence ≥ 0.8 + the same drift target two ticks running → auto-advance even outside the window (spec 13); a lapsed stage's uncovered moments archive once and stop appearing as gaps; a quiet multi-day night ticks `mode: "idle"` with zero model spend.
- [ ] Guardrails hold under adversarial LLM output (action fuzz test: invalid actions are rejected and logged, never applied).
- [ ] Two consecutive ticks with no changes → `NO_OP` with reasoning (no bounty spam).
