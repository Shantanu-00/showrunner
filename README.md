<p align="center">
  <img src="frontend/public/logo.png" width="104" alt="Showrunner">
</p>

<h1 align="center">Showrunner</h1>
<p align="center"><b>The autonomous media director for live events.</b></p>

<p align="center">
<b>The Twist: when event coverage is missing, the agent doesn't wait for humans — it tasks them.</b>
</p>

Guests scan a QR code and upload photos from their own phones. A fleet of agents classifies every
shot, indexes faces, screens for dignity and safety, runs the venue's big screen, detects **coverage
gaps** against the event's own timeline, dispatches photo **bounties** to guests' phones, and directs
beat-synced highlight reels with AI-composed soundtracks — **while the event is still happening.**

**There is no chat window anywhere in this product.** Nothing here is a prompt box with a nicer
skin: the system observes, decides and acts on a schedule, and a human's total involvement is one
paste at setup.

<p align="center">
  <a href="https://showrunner-hq.web.app/">🚀 <b>Live demo</b></a> ·
  <a href="https://showrunner-hq.web.app/how-it-works">📐 <b>Guided tour</b></a> ·
  <a href="#8-architecture">🏗 <b>Architecture</b></a> ·
  <a href="#15-spin-up-two-tiers">⚙️ <b>Spin-up</b></a> ·
  <a href="{{VIDEO_URL}}">▶ <b>Demo video</b></a> ·
  <a href="{{BLOG_URL}}">📝 <b>Build log</b></a>
</p>

> **Category: The Taskmaster** — a multi-step background workflow the agent intercepts and completes
> **without human intervention**. Built **solo** by Shantanu
> ([`Shantanu-00`](https://github.com/Shantanu-00)) for the All Things Agentic Hackathon.

<!-- BEFORE SUBMITTING: fill {{VIDEO_URL}}, {{BLOG_URL}} and {{SOCIAL_URL}}. Grep for `{{` — every
     occurrence is in one of three places: the link row above, the compliance table below (the video
     row and the bonus row), and the License footer. Nowhere else in the file. -->

<p align="center"><img src="docs/assets/architecture-diagram.jpg" width="920" alt="Showrunner architecture — an event-driven data plane feeding a goal-driven control plane, with governance as a cross-cutting rail"></p>

---

## Contents

| # | Section | For the reader who wants… |
|---|---|---|
| 1 | [Hackathon compliance at a glance](#1-hackathon-compliance-at-a-glance) | the requirement checklist, with code paths |
| 2 | [What Showrunner is](#2-what-showrunner-is) | the product, in 90 seconds |
| 3 | [The problem (Bring Your Own Friction)](#3-the-problem-bring-your-own-friction) | why this exists |
| 4 | [The autonomous loop](#4-the-autonomous-loop-why-this-is-a-taskmaster) | the Taskmaster mechanism |
| 5 | [It runs for the whole event](#5-it-runs-for-the-whole-event-long-running) | long-running / durability |
| 6 | [Try it in 60 seconds (judges)](#6-try-it-in-60-seconds-judges) | to touch the live thing now |
| 7 | [Complete feature inventory](#7-complete-feature-inventory) | everything that is built, per surface |
| 8 | [Architecture](#8-architecture) | repo map, state, isolation, failure handling |
| 9 | [The agent fleet (an honest census)](#9-the-agent-fleet-an-honest-census) | what is genuinely an agent, and what isn't |
| 10 | [Google Cloud services](#10-google-cloud-services--used-and-called-in-code) | proof of GCP usage in code |
| 11 | [Deliberately NOT used](#11-deliberately-not-used-and-why) | the engineering-judgment table |
| 12 | [Bonus Google AI models](#12-bonus-google-ai-models--each-with-a-real-job) | Lyria / Veo / Gemma, each with a job |
| 13 | [Trust architecture](#13-trust-architecture-consent-dignity-guardrails) | consent, dignity, guardrails |
| 14 | [Scale & cost](#14-scale--cost-production-readiness) | production-readiness math |
| 15 | [Spin-up (two tiers)](#15-spin-up-two-tiers) | reproducible setup instructions |
| 16 | [What you can verify yourself](#16-what-you-can-verify-yourself) | the checkable evidence, and what it proves |
| 17 | [Challenges, findings & learnings](#17-challenges-findings--learnings) | the honest platform-friction report |
| 18 | [Honest limits](#18-honest-limits-designed-not-built) | what is **not** true yet |
| 19 | [Disclosures](#19-disclosures) | licences, AI assistance, dataset provenance |

---

## 1. Hackathon compliance at a glance

| Requirement | Status | Where in the code |
|---|---|---|
| **Gemini 3.5 or newer** via Gemini API / Vertex AI | ✅ `gemini-3.5-flash-lite` (perception volume, per-photo) + `gemini-3.7-flash` (director reasoning, itinerary understanding) | `backend/services/gemini.py`, called from `backend/workers/{curate,safety}/agent.py`, `backend/directors/{story,reel}/agent.py`, `backend/api/host.py::_itinerary_agent` |
| **≥1 Google Agent Framework** | ✅ **ADK v2 (Python)** + **GenAI SDK** — every model call is an ADK `LlmAgent` behind one runner, with an ADK plugin for Model Armor | `backend/services/gemini.py`, `backend/services/armor_plugin.py`, `backend/workers/*/agent.py`, `backend/directors/*/agent.py` |
| **≥1 GCP infrastructure service** | ✅ **8 Cloud Run services + 1 Cloud Run Job**, Firestore (+ native vector search), Cloud Storage (3 buckets), Eventarc, Cloud Tasks (6 queues), Cloud Scheduler, Cloud Vision, Model Armor, Secret Manager, Cloud Logging | [full service table →](#10-google-cloud-services--used-and-called-in-code) |
| **Hosted project URL** | ✅ [`showrunner-hq.web.app`](https://showrunner-hq.web.app/) — a real front door (Create · Join · [See how it works](https://showrunner-hq.web.app/how-it-works)). Host-console credentials are in the Devpost testing-instructions field | live through the judging period |
| **Architecture diagram** | ✅ **One page, high-level workflow only: [`architecture-simple.pdf`](docs/assets/architecture-simple.pdf)** — six labelled stages, one sentence per box, every agent named with its framework, its model and where it runs. A 7-page [`architecture.pdf`](docs/assets/architecture.pdf) carries the low-level design behind it (deployment topology &amp; identity · data plane · control loop, state &amp; memory · reel pipeline · trust &amp; governance · failure, scale &amp; cost). Every source is checked in and re-renderable with `python scripts/render_architecture.py` | `docs/architecture.md` |
| **Reproducible spin-up instructions** | ✅ [two tiers](#15-spin-up-two-tiers) — **Tier 0 needs no GCP account at all** and still runs **164 real assertions** | `deploy/bootstrap.sh`, `deploy/up.sh`, `Makefile` |
| **Demo video ≤ 4 min, public, GCP proof** | ✅ {{VIDEO_URL}} — **unedited live execution** plus Cloud Run / Cloud Scheduler / Firestore console proof | — |
| **Bonus: additional Google AI models** | ✅ **Lyria 3** (every reel's soundtrack) · **Veo 3.1 Fast** (cinematic reel opener) · **Gemma 4** (private taste memos) | [bonus table →](#12-bonus-google-ai-models--each-with-a-real-job) |
| **Bonus: content piece + social post** | 🚧 a build-log article stating it was created for this hackathon, plus a `#AllThingsAgenticHackathon` post — both publish before submission | {{BLOG_URL}} · {{SOCIAL_URL}} |

**Rubric keywords, so a triage pass can find them:** autonomous · beyond standard chat loops ·
multi-step background workflow **without human intervention** · Bring Your Own Friction · the Twist ·
**long-running** · durable · restartable · cross-revision continuity · **state management** ·
**context rot** · **tools properly isolated and scoped for security** · clean, modularized ·
**failure-tolerant** · unedited live execution · reproducible setup · visual proof of Google Cloud
deployment · findings and learnings.

---

## 2. What Showrunner is

**One paragraph.** Showrunner is an autonomous media director for a real-world event. It has an
**event-driven data plane** (phones → Cloud Storage → Eventarc → rate-limited Cloud Tasks → three
perception workers → Firestore, whose realtime listeners drive every screen) feeding a **goal-driven
control plane** (two director agents that wake on a Cloud Scheduler tick, compare what the timeline
says *should* be happening against what the photo stream *proves* is happening, and act on the
difference). Governance is a cross-cutting rail: agents write opinions, and one deterministic,
transactional function decides what any human is allowed to see.

### The three humans, and what each of them does

| Who | What they do | What they never do |
|---|---|---|
| **Host** | Paste an itinerary (text, PDF, or a photo of a schedule). Review the table Gemini proposes. Tap the people who matter. Press **Go Live**. | Sort photos. Chase a photographer. Write a prompt. Answer a chatbot. |
| **Guest** | Scan a QR, tap once to join (anonymous — no account, no app install), send photos. Optionally take a selfie to unlock a private album *of themselves*. Get a push notification when the director needs a shot, and answer it. | Install anything. Create an account. Manually tag anyone. |
| **The wall** (a TV, a projector, a laptop in a corner) | Open `/kiosk/{eventId}` once. | Get touched again. |

Everything else is the agent fleet: **five components**, of which **four are genuinely agents** and
one is honestly labelled a specialized ML worker ([census →](#9-the-agent-fleet-an-honest-census)).

### Creation is itinerary-led, not template-led

There is **no** event-type picker, no religious template grid, no card menu of occasions. The
`EventTemplateId` enum and its nine preset profiles were **deleted** from this codebase, on purpose:
they were decoration over a handful of dials that the host can simply set.

What replaced them is a single call — `POST /v1/itinerary/extract`
(`backend/api/host.py:934`) — that takes a paste, a PDF, or a screenshot and returns a **whole
proposed event**, produced by `gemini-3.7-flash` behind an ADK `LlmAgent`:

| Extracted | Example | Reviewed by the host in |
|---|---|---|
| `suggestedName` | *"Japan 2026 — Tokyo + Kyoto"* (synthesized if the source has no title) | Step 1 |
| `startDate` / `endDate` | inferred from "Oct 12–16" or "Saturday May 14" | Step 1 |
| `timezone` | IANA name inferred from cities/airports (*Haneda → `Asia/Tokyo`*) | Step 1 |
| `expectedParticipants` | *"4 of us"* → `4`; *"150 guests"* → `150` | Step 1 |
| `suggestedAccessMode` | `invite` for a private trip, `open` for a public venue party | Step 1 |
| `culturalGlossary` | `["shinkansen", "izakaya", "torii gates"]` — constrains what the Curator may *claim* about a photo | Settings |
| `suggestedPeople` | named people with a role and a VIP tier, offered as one-tap chips that prefill the enrolment form | Step 3 |
| `stages[]` | every phase in chronological order, each with `timeHint`, a proposed local start/end, `requiredMoments`, an `expectedSetting` and a kiosk `theme` | Step 2 |

**The model's output is a proposal, never a decision.** `PUT /v1/events/{eventId}/stages` is the
only writer of real UTC stage windows, and it only ever writes the host-edited table
(`backend/api/host.py:1099`). Every extracted string that will later ride into a per-photo prompt —
the paste itself, the text transcribed out of a PDF, the glossary, the stage labels — is screened by
**Model Armor** first (`armor.guard`, five ingress surfaces in `backend/api/host.py`).

**Nothing in the pipeline is wedding-specific,** and three seeders ship to prove it — all three
uploading **through the real pipeline** (signed URL → GCS → Eventarc → the three workers), never by
writing Firestore directly, because judges may inspect:

| Seeder | Event | What it is for |
|---|---|---|
| `make seed` | a Hindu wedding (`dev_demo`, 6 AI-generated cast members, 25 golden fixtures) | the graded fixture set `make eval` scores, and the development event every smoke script uses |
| `make seed-trip` | a **5-day Japan trip for four friends** (`japan_trip_2026`, 10 stages across 5 days) | the multi-day, generic-timeline demo — positioned so *today* is Day 4 with a real, open group-coverage gap for the director to find |
| `python scripts/seed_global_event.py` | the standing global demo (`global_demo`) — **zero media, no cast, no venue, no theme** | the always-live public tour: generic week-chapter stages and volume ceilings instead of a time limit, filled entirely by real visitors |

**And an honest note on how it scales *down*,** because it is a design property rather than a caveat:
bounties exist because there are more moments than photographers, and issuance is driven by the
coverage ledger. At five guests there are no statistical gaps, so the director correctly issues
**none**. The system degrades by event size because the control loop is evidence-driven, not
template-driven.

---

## 3. The problem (Bring Your Own Friction)

Every trip, every birthday, every family gathering ends the same way: photos scattered across a
dozen phones, a group chat that becomes a lossy archive, a week of *"can you send me that one?"* —
and nobody ever gets the photos *of themselves*. **That aftermath is the chore.** It happens to all
of us several times a year, and more storage has never fixed it, because the problem is direction
and routing, not capacity.

Last December I watched it at its worst. At my cousin's wedding, five hundred guests took over four
thousand photos. They died in phone galleries and one group chat. The couple got their
photographer's album **five months later**. And the moments nobody thought to shoot — the groom's
mother during the haldi — were simply never captured, because nobody was directing five hundred
amateur photographers.

A wedding is the same chore at maximum intensity: five hundred phones instead of five, so the
failure becomes visible instead of merely annoying. That is the messy, multi-step chore Showrunner
intercepts — **while the event is still happening, so there is no aftermath to clean up.**

The process it replaces took five months.

---

## 4. The autonomous loop (why this is a Taskmaster)

Every **2 minutes**, with **no human in the loop**, the Story Director runs on Cloud Run *inside the
Cloud Scheduler tick that already holds the per-event lease*:

```
Validate → Expire → Arm → LEDGER → REASON → ACT
```

| Phase | What happens | LLM involved? |
|---|---|---|
| **Validate** | Bounty submissions are judged: the *moment* by Gemini (one text-only question), the *identity* by the deterministic 512-d ArcFace pipeline. Points are awarded transactionally — never twice. | one text-only call |
| **Expire** | A bounty past its window is closed. An **unfulfilled** one is archived into `permanentGaps` — an **admitted coverage gap**, not a silent drop. An assigned-but-unanswered ask is released to broadcast. | no |
| **Arm** | Upcoming required moments are armed against the schedule. | no |
| **LEDGER** | A deterministic coverage ledger reconciles the timeline against the photo stream: per-stage counts, per-person coverage (median-relative), a `peopleBuckets` group histogram, and a **stage-drift** signal from the last 20 indexed photos. | **no — this is pure Python** |
| **REASON** | One `gemini-3.7-flash` call sees the ledger and a rolling 10-tick session window, and proposes actions. | yes |
| **ACT** | Every proposed action is validated by a **pure, no-I/O executor** (`backend/directors/story/act.py`) before anything is written: bounty caps, point clamps, duplicate-target rejection, hallucinated-`personId` rejection, stage-advance confidence thresholds. | no |

### Judged on outcomes, not mechanisms

- **A guest un-consents and a published film comes off the wall in under five seconds.**
  `backend/shared/reels.py::retract_containing` — measured live at **5.6 s**.
- **The agent tasks five hundred guests — the crowd becomes the crew.** A bounty is issued to
  everyone holding a phone, not to a hired photographer.
- **It writes its own wrap-up report, including what it failed to get.** An expired, unfulfilled
  bounty is a permanent, admitted gap in that report.

The guardrails behind those outcomes — `recompute_visibility`, the bounty budget/point clamps, the
107-assertion rules matrix — are real, but they are the **Architecture** story ([§8](#8-architecture)),
not the headline. The headline is what the system does without being asked.

### The ask has to reach a pocket, or the loop is only half a loop

Directing a crowd that is not looking at its phones is directing nobody. A bounty is delivered by
**Web Push** as well as by the in-app banner and the kiosk wanted-poster
(`backend/shared/push.py` → `frontend/public/firebase-messaging-sw.js`). Three properties of that
path are deliberate:

1. **The audience is resolved deterministically** from the bounty's own `audience` field — one uid
   for an assigned ask, the guests whose `lastSeenAt` puts them inside the `nearStage` window for a
   local one, everyone otherwise — reusing the same query `act.resolve_assignee` uses. **A model
   never picks a recipient**, any more than it picks an assignee.
2. **The registration token is treated as an address.** It lives in a deny-all
   `guests/{uid}/private/push`, not on the leaderboard document whose own rule says *"no email, no
   phone, no token."*
3. **Sending is off the critical path.** `notify_bounty` never raises, so an FCM outage costs a
   notification and never a tick, a bounty, or a guest's points. Dead tokens are pruned on the send
   that proves they are dead — which is why there is no sweep case for them.

Two platform facts, stated as limits rather than gaps: **iOS grants Web Push only to an installed
PWA** (16.4+), so the opt-in row detects that and shows the *Share → Add to Home Screen* gesture
instead of a button that cannot work; and the service worker deliberately **imports no Firebase
SDK** — FCM on the web *is* W3C Web Push underneath, and a notification that has to arrive over
hotel wifi is a poor place to depend on a runtime CDN.

### Uploads flow the same way, just faster

A photo goes from a phone to the right surfaces in **~6 seconds warm**, with zero human touches:
`3.6–4.4 s` of pipeline (four stages, three of them concurrent) plus `1.3–3.7 s` for the publisher to
rebuild the ranked program. On a first upload after idle it is ~42 s, because two workers run at
`min-instances 0` — which is exactly what the warm-up hook exists to remove.

### And the guests get the film

A published `event_recap` appears on the phone of anybody who was actually at the event, with a save
button (`frontend/src/components/me/RecapCard.tsx`). Watching and keeping are gated **differently,
on purpose**: playback stays reachable without a session, because a `<video>` on a venue TV cannot
send an `Authorization` header — but `?download=1` requires event membership on *every* event. A
downloaded file leaves the consent interlock behind for good, and the retraction path can pull a reel
off every surface it still controls while having no reach into a camera roll.

---

## 5. It runs for the whole event (long-running)

The Taskmaster brief asks for something long-running *from a user's perspective* — hours, weeks,
sometimes months. Showrunner is built to that standard, not to a demo's runtime.

| Property | How it works | Code |
|---|---|---|
| **A real lifecycle, not a flag** | `draft → live → paused → wrapping → wrapped` is the event's master switch. A five-day trip walks it once; `wrapping` is what commissions the recap film and writes the honest wrap report. | `backend/schemas/host.py`, `backend/api/host.py` |
| **A 2-minute transactional lease drives the control loop** | With **leader election per event** (`publisherLease/{eventId}`, `ticks/{eventId}`) rather than a global singleton — so two live events never contend for one process, and a scaled-away instance's lease expires cleanly to the next one. | `backend/shared/leases.py` |
| **At-least-once delivery, idempotent everywhere** | Eventarc and Cloud Tasks both redeliver. Every handler is keyed on a Firestore **status transition**, never on a named task, so a replay is a no-op rather than a double-charge. | `backend/shared/pipeline.py` |
| **Two-layer retry, then a decision — never a retry storm** | A transient model error retries **3× in-process** with quadratic backoff (2 s, 8 s) inside one ADK invocation; if it still fails, the Cloud Tasks queue backs the whole stage off for up to **5 attempts** before quarantining with a `severity=error` ops alert. A *permanent* failure (a refusal, an invalid schema) writes the stage's conservative default and completes. | `backend/services/gemini.py::_invoke`, `backend/shared/pipeline.py` |
| **DLQ quarantine + per-stage surgical replay** | Not a redeploy. `POST /media/{id}/review` (host override) and `POST /admin/replay/{mediaId}?stage=` re-run exactly one stage with a fresh attempt budget — hours or weeks after the original failure. | `backend/api/moderation.py` |
| **Split-brain-safe reconciliation** | Face clusters are claimed at the *face* level and merged by a nearest-neighbour sweep on the hourly job, rather than assumed consistent in real time — so two concurrently-forming clusters for the same person self-heal instead of staying split forever. | `backend/api/sweep.py` |
| **Zero-token idle ticks** | Overnight on a multi-day event, a tick with no active stage and nothing in flight resolves to `mode: "idle"` and makes **no model call at all**. Long-running does not mean continuously billing. | `backend/directors/story/director.py` |
| **Lapsed-stage semantics** | `activeStage` is a *pin that expires* at its own stage's `endsAt + 90 min`, so the first auto-advance can't permanently strand a five-day event's stage pointer. An advance may never target a lapsed stage — a Day-1 batch dumped on Day 3 cannot walk the event backwards. | `backend/shared/stages.py::pin_has_lapsed` |
| **Cross-revision continuity** | Every piece of state that matters lives in Firestore or a bounded session window — never in a process's memory — so a Cloud Run revision restart, a scale-to-zero or a redeploy loses nothing. **A live redeploy mid-build proved this directly:** a new publisher instance picked up all nine of a terminated instance's event leases and kept every kiosk wall running without a gap. | `backend/publisher/supervisor.py` |

> ### ⚠️ Read this before you create your own event: it auto-wraps after 60 minutes
>
> **Any event you create through the public wizard is `class: public`, and a public event auto-wraps
> 60 minutes after Go Live.** The hourly `orphan-sweep` calls the same `wrap` → `finalize` pair the
> host's own buttons call, with a synthetic system principal standing in for the host who isn't there
> to press them (`backend/api/sweep.py::_sweep_guardrails`). It is **not** a bug and it is **not**
> being removed — it is the governor that makes a public, unauthenticated "Create an event" button
> safe to leave on the internet for a month. It has two siblings:
>
> | Rail | Value | Env var | What it does |
> |---|---|---|---|
> | TTL auto-wrap | **60 min** after `liveAt` | `PUBLIC_EVENT_MAX_LIVE_MINUTES` | `live`/`paused` → `wrapping` → `wrapped`; frees the capacity slot |
> | Cost ceiling | **$3.00** | `PUBLIC_EVENT_COST_CEILING_USD` | pauses uploads + a `severity=warning` ops alert; reversible by the host |
> | Concurrency cap | **3** live public events | `MAX_CONCURRENT_LIVE_EVENTS` | transactional gate at Go Live against `platform/liveEventCount` |
>
> The sweep runs hourly, so the real kill window is **60–120 minutes**, and nothing warns you first:
> the event is simply `wrapped`, uploads are refused, and the kiosk is gone.
>
> **This is why a multi-day trip cannot be demonstrated on a wizard-created event.** Three ways past
> it, in the order they are worth reaching for:
>
> 1. **Use the seeded trip.** `make seed-trip` builds `japan_trip_2026` as `class: internal_dev`,
>    which is exempt from all three rails — a real 5-day, 10-stage, 4-participant event with today
>    positioned as Day 4 and a live group-coverage gap.
> 2. **Hold `platformAdmin`.** The deployment owner's own uid carries that claim, so their wizard
>    events are assigned `internal_dev` rather than `public`, and are likewise exempt. This is the
>    intended path for the person running the deployment.
> 3. **Raise the value** on the deployed `api`:
>    `gcloud run services update api --region us-central1 --update-env-vars PUBLIC_EVENT_MAX_LIVE_MINUTES=10080`.
>    Do this knowing what it switches off — an abandoned public event then holds a capacity slot and
>    keeps its $3 ceiling as the only brake.
>
> **Known rough edge, stated rather than papered over:** the TTL is a flat 60 minutes and ignores
> `Event.startsOn`/`endsOn`. An event that has *told the system* it runs for five days is still
> wrapped after one hour, which is wrong on its own terms. Deriving the TTL from the declared date
> range (plus a grace window), falling back to 60 minutes only for an undated event, is the correct
> fix and **is not built**.

---

## 6. Try it in 60 seconds (judges)

[**showrunner-hq.web.app/how-it-works**](https://showrunner-hq.web.app/how-it-works) is a guided tour
of a real, always-live global demo event (`class=protected_demo`) — no cast, no script, no
pre-loaded photos. Every configuration difference from an ordinary event is disclosed **on the page
itself**, not hidden.

**Everything the wall shows was put there by real visitors.** There is no seeded cast, no scripted
timeline and no pre-loaded photo set: the running order, the captions and the countdowns were decided
by the agents, from whatever arrived. If the wall is quiet when you land, step 3 is how you fill it —
which is a stronger demonstration than a pre-baked slideshow would be.

| Step | What to do | What proves itself |
|---|---|---|
| 1 | **Put the show on a big screen** — open `/kiosk/global_demo` and press *Start show* | Fullscreen, wake lock and sound. Every slot is ranked by a deterministic score the publisher stores on the playlist — no client-side ranking, no polling |
| 2 | **Join as a guest on your phone** — two taps from the QR code | Silent anonymous sign-in: no install, no sign-up, no email address |
| 3 | **Send three sample photos** — they ship with the page, so no file picker is needed. Tap *"Share to the big screen"* | Each thumbnail carries **the real state of its own pipeline**: uploading → the Curator is judging your shot → looking for faces → safety → **live on the wall**. ~6 s end to end, warm |
| 4 | **Wait for a mission — nobody presses anything** — the countdown on the page is a real Cloud Scheduler cron, read from `GET /v1/events/{id}/public`'s `director` block | When it reaches zero and the Story Director finds a moment with no coverage, a mission banner appears on the phone and a wanted poster takes over the wall. **There is no button in this step — that is the point of it** |
| 5 | **Answer the director** — tap *Shoot now*, send another photo | Identity in the award is a 512-d ArcFace match, **never** a language model — the model is asked exactly one *text-only* question, so the service running the planner has no grant to read a guest's photo at all. Points land within one tick, with a confetti burst |
| 6 | **Poke at consent** — pull a photo back, or remove yourself from the wall as a *subject* rather than the uploader | Retroactive within seconds. This is the part worth attacking |
| 7 | **Look at the host's side** — create your own event (under a minute, no account), or use the console credentials in the Devpost submission instructions | Real state machine, real aggregates, review queues, stage overrides, and a hold-to-confirm **Freeze public** switch that clears every public surface in about two seconds |
| 8 | **The receipts** — Cloud Run, Cloud Scheduler and a saved Logs Explorer query, plus `backend/directors/story/act.py` itself | Every number that decides who gets paid, in one file, **with no I/O** — checkable without a cloud account |

Host-console credentials are deliberately **not** printed on the public tour page: a host link is a
bearer credential that can freeze or wrap the event other judges are looking at. They live in the
Devpost testing-instructions field, which is what the rules ask for, and the page says where to find
them.

The manual override below the fold (*"Force a tick now"*) is labelled exactly that — an escape hatch
for a judging-month cadence, not the demo. **The autonomy claim rests on the schedule, never on a
button.**

**Why the standing demo can stay open for a month.** `class: protected_demo` exempts it from the
public 60-minute TTL and the $3 ceiling — it has to stay live indefinitely, so *time* cannot be its
guardrail. **Volume** is instead: `dailyMediaCap` / `lifetimeMediaCap` bound the number of
Gemini/Vision calls this event can ever generate, and `reelCommissionEveryNMedia` stops a trickle of
uploads from earning a fresh Veo/Lyria film on every tick. All three are ordinary `Event` fields any
host could set — **not** a demo-only branch (`backend/schemas/event.py`, enforced in
`backend/api/uploads.py::_register_batch`).

---

## 7. Complete feature inventory

Everything below is **built and in the repository**. Things that are designed but not built are in
[§18](#18-honest-limits-designed-not-built) instead — this project's rule is that an unbuilt row gets
**deleted or named, never softened**.

### 7.1 Setup & onboarding (host)

| Feature | What it does | Code |
|---|---|---|
| **AI-first event extraction** | One paste / PDF / screenshot → a whole proposed event: name, date range, IANA timezone, headcount, access mode, cultural glossary, key people with tiers, and a full stage table. `gemini-3.7-flash` behind an ADK `LlmAgent`. | `backend/api/host.py::extract_itinerary` |
| **Four-step wizard** | **Setup → Timeline → People → Launch.** Each step reviews and edits what the model proposed; nothing is saved by the model itself. | `frontend/src/components/host/HostWizard.tsx` |
| **Three input modes** | Plain-text paste, PDF upload, or a photographed schedule — the same endpoint, inline bytes at full resolution (deliberately *not* the low-resolution setting the perception workers use: this is "read the small text in this screenshot", once per wizard). | `frontend/src/components/host/ItineraryInputTabs.tsx` |
| **Reviewable stage editor** | Per stage: label, `datetime-local` start/end prefilled from the model's proposal, required moments, expected physical setting, kiosk theme. Add, delete, reorder. | `frontend/src/components/host/StageEditorCard.tsx` |
| **AI-identified people chips** | Named people the model found in the itinerary become one-tap chips that prefill the enrolment form with a name, a role and a VIP tier. Already-added people show a ✓. | `HostWizard.tsx::PeopleStep` |
| **Host-side enrolment** | A host can enrol a person from a photo they hold, without that person present — and **no uid is ever linked** by this path, so enrolment can never grant album access to the wrong human. | `backend/api/identity.py::/people/host-enroll` |
| **Equal-coverage mode** | One checkbox flips the default tier for newly-added people between *Inner Circle* and *Guest*, which is what actually reweights kiosk ranking and reel selection. | `HostWizard.tsx::PeopleStep` |
| **Anonymous creation + recovery** | An event is created with no account. Step 4 hands over a **recovery code** (the only way back in if every signed-in device is lost), a co-host link, and the guest join URL + QR. Google sign-in is an *optional post-creation upgrade*. | `backend/api/host.py`, `frontend/src/components/host/GoogleUpgradeCard.tsx` |
| **Access modes** | `open` (anyone with the link) or `invite` (a hashed, rotatable join code with a seat cap, minted in the create request). Downgrading `invite → open` requires an explicit confirm and shows the consequence copy verbatim. | `backend/api/host.py::/access`, `/access/code`, `/access/seats` |

### 7.2 The guest's phone (PWA)

| Feature | What it does | Code |
|---|---|---|
| **Two-tap send** | Scan → anonymous sign-in → pick photos → choose a consent ring → send. No account, no app store. | `frontend/src/components/join/SendSheet.tsx` |
| **Offline-durable outbox** | Upload state survives app close in IndexedDB, with a concurrency-3 drain loop and exponential backoff. Upload **intent is registered in Firestore before bytes move**, so orphans are detectable and consent is never ambiguous. | `frontend/src/lib/{outbox,uploadManager}.ts` |
| **Live pipeline chips** | Per-photo state read from the real per-media state machine — curating → faces → safety → live. Not a fake progress bar. | `frontend/src/components/gallery/StageChips.tsx`, `join/Filmstrip.tsx` |
| **Public gallery** | Recent / Highlights, a **two-tier day-then-stage filter** for multi-day events, and a lightbox. | `frontend/src/components/gallery/{PublicGallery,StageChips,Lightbox}.tsx` |
| **"Why this photo?"** | An explain view rendered purely from stored `curator` fields — zero gate recomputation, so it can't disagree with the decision it explains. | `frontend/src/components/gallery/WhyThisPhoto.tsx` |
| **Private album of yourself** | An optional selfie unlocks an album driven by the `albumOf` listener: every photo you appear in, including ones you never took. | `frontend/src/components/me/AlbumGrid.tsx` |
| **Selfie enrolment ritual** | Live-camera-only capture, un-pre-ticked consent, and all three backend outcomes handled (claimed / held for review / ambiguous). | `frontend/src/components/me/EnrollRitual.tsx` |
| **Bounty banner + award burst** | The director's ask lands as an in-app banner and a Web Push notification; answering it produces a points award animation. | `join/BountyBanner.tsx`, `join/AwardBurst.tsx` |
| **Missions & leaderboard sheets** | Open asks and a leaderboard that shows *"Mystery guest 🎭"* for anyone unenrolled — the leaderboard document's own rule forbids email, phone or push token. | `gallery/{MissionsSheet,LeaderboardSheet}.tsx` |
| **Timeline sheet** | The event's own schedule on the guest's phone. Stage **windows** are member-only; a stranger gets day granularity, never times. | `gallery/TimelineSheet.tsx` |
| **The recap film** | A published `event_recap` on the phone of anybody who was there, with a save button. `?download=1` requires event membership on every event. | `me/RecapCard.tsx` |
| **Push opt-in** | Detects iOS's installed-PWA requirement and shows the *Add to Home Screen* gesture instead of a button that cannot work. Renders nothing at all when the VAPID key is unset. | `me/PushOptIn.tsx`, `frontend/src/lib/push.ts` |
| **Consent, veto, delete** | The 3-ring chooser, a per-photo padlock, a subject veto toggle, and *delete my data* — which really deletes face documents, the 512-d embedding, the private profile, the push registration, and tombstones uploads. | `consent/{RingChoiceSheet,PadlockSheet}.tsx`, `backend/api/identity.py::DELETE /people/me` |

### 7.3 The wall (kiosk)

| Feature | What it does | Code |
|---|---|---|
| **A dumb client of one document** | The kiosk renders only what the publisher already decided in `kiosk/playlist`. No client-side ranking, no polling — one Firestore listener. | `frontend/src/components/kiosk/KioskShow.tsx` |
| **Deterministic hero ranking** | `aesthetic × recencyDecay(20 min half-life) × diversityPenalty × stageMatch × vipWeight`, as a **pure function**. | `backend/publisher/program.py` |
| **Face-anchored Ken Burns** | `transform-origin` set to the largest detected face box, so the slow zoom lands on a person rather than a wall. | `kiosk/slots/` |
| **Just-in strip, credits, wanted poster** | A live "just arrived" filmstrip, an end-credits leaderboard, and the director's bounty rendered as a wanted poster with an enlarged QR. | `kiosk/Overlays.tsx`, `kiosk/slots/` |
| **Reel premiere** | A commissioned reel premieres on the wall when it finishes rendering. | `kiosk/slots/` |
| **Slide prefetch** | The next 3 slides are prefetched with a shared blob LRU (48 entries) for the authenticated path, so a slow venue network doesn't show a blank slot. | `frontend/src/lib/kioskPrefetch.ts` |
| **Stage-of-the-moment theming** | `data-stage-theme` re-themes the wall from the playlist's `theme` field through a closed 8-name palette vocabulary; anything unrecognised degrades to the default palette rather than to a broken one. | `KioskShow.tsx`, `frontend/src/design/tokens.css` |
| **Offline loop + start gesture** | A cached playlist keeps looping if the network drops; one Start-show gesture takes fullscreen, a wake lock and audio unlock. | `kiosk/KioskShell.tsx`, `kiosk/KioskSetup.tsx` |
| **Frozen-event branch** | A frozen event builds an **empty** program, not a stale one — the panic switch cannot be defeated by a cache. | `backend/publisher/runner.py` |

### 7.4 The director (the autonomous part)

| Feature | What it does | Code |
|---|---|---|
| **Coverage ledger** | Deterministic, no LLM: per-stage counts, required-moment coverage, per-person coverage (median-relative, silent under 3 people or a median < 4), a group-shot histogram, and a stage-drift signal. | `backend/directors/story/ledger.py` |
| **Four gap kinds** | `moment`, `vip`, `vip_thin`, `group` — each with its own fulfilment test. | `ledger.py`, `backend/shared/coverage.py` |
| **Guardrailed actuation** | Every model-proposed action passes a **pure, no-I/O** validator: unknown stage, hallucinated `personId`, duplicate target (within a plan *and* across ticks), 2-per-tick budget, 6-open ceiling, copy-length bounds, escalation legality, points clamped to `[50, 300]` with VIP weighting. **A looping or hallucinating turn is rejected and logged, never applied.** | `backend/directors/story/act.py` |
| **Targeted asks** | `audience=assignee` is resolved deterministically in ACT and released to broadcast by EXPIRE — **assignment never changes pay.** | `act.py::resolve_assignee` |
| **Evidence-driven stage advance** | A drift streak of ≥2 ticks beats the schedule window. High confidence *and* schedule agreement applies automatically; anything less becomes a host suggestion, which is a good outcome rather than a failure. | `act.py`, `backend/shared/stages.py` |
| **Kiosk escalation** | An unfilled critical bounty escalates to a wall takeover. | `act.py` |
| **World model** | A distilled venue paragraph generated from `sceneSetting` counts, so the director can reason about siting without a per-photo dump in the prompt. | `backend/directors/story/world.py` |
| **Event Diary** | A per-lapsed-stage `flash-lite` memo, consumed by the recap brief, the wrap headline and director copy — and **never** by ranking, visibility or points. | `backend/directors/story/diary.py` |
| **Wrap report** | Written at `wrapping`: day labels, best media, gap detail, the recap reel id, and **what the director failed to get**. | `backend/api/host.py::/lifecycle/finalize` |
| **Reel Director** | `SELECT → DIRECT → CRITIC → EDL → RENDER → PUBLISH`: evidence to narrative brief to edit decision list, with a generator+critic self-correction loop, a storyboard linter, a Lyria soundtrack, beat-snapped cuts and a face-safe crop check. | `backend/directors/reel/` |
| **Render as a Cloud Run Job** | ffmpeg + a librosa beat grid at 8 vCPU, entirely off the request path. One active render per persona per event. | `backend/render/main.py`, `directors/reel/store.py::in_flight_of_persona` |

### 7.5 Host console

| Panel | What it does | Code |
|---|---|---|
| **Lifecycle** | `draft → live → paused → wrapping → wrapped`, with a transactional capacity gate at Go Live. | `host/LifecyclePanel.tsx` |
| **Freeze public** | One tap empties every public surface in under five seconds. Reversible. | `host/FreezeButton.tsx`, `backend/api/host.py::/freeze` |
| **Review queue** | Every Guardian `host_review` verdict, overridable — so a conservative default always has a reachable escape hatch. | `host/ReviewPanel.tsx`, `backend/api/moderation.py` |
| **Claim review** | A selfie that matches a protected/enrolled person is **held** rather than auto-claimed; the host approves, denies, or reverses a prior decision. | `host/ClaimReviewPanel.tsx`, `backend/api/identity.py::/claims/{claimId}/review` |
| **People** | Enrol, tier, and re-tier participants. | `host/PeoplePanel.tsx`, `backend/api/identity.py::/people/{personId}/tier` |
| **Itinerary** | Re-parse a corrected itinerary against a live event; `PUT /stages` stays the only writer. | `host/ItineraryPanel.tsx` |
| **Settings** | The sensitivity dials (`pda`, `alcohol`, `attire`), the cultural glossary and required-moment templates. **The dials alone are editable while the event runs**, because a dial can only hold a photo back, never release one already judged — a host tightening one mid-event is a safety action the console must not refuse. | `host/SettingsPanel.tsx`, `backend/api/host.py::update_profile` |
| **Access** | Invite code rotation, seat cap, kiosk visibility. | `host/AccessPanel.tsx` |
| **Stage override** | Pin the active stage by hand; the pin is respected over any model advance. | `host/StageOverridePanel.tsx` |
| **Tick countdown** | The live next-tick clock, read from the same public payload the tour uses. | `host/TickCountdown.tsx` |
| **Wrap report** | The honest end-of-event report, including admitted gaps. | `host/WrapReportPanel.tsx` |
| **Recovery** | A recovery code, plus a `platformAdmin` escape hatch for a host who has lost every device. | `backend/api/host.py::/recovery-code`, `/admin/recovery-code` |

### 7.6 Platform rails

| Rail | What it does | Code |
|---|---|---|
| **Concurrency cap + kill switch** | `platform/liveEventCount` gates Go Live transactionally; `platform/publicCreationEnabled` is a one-write stop on new public events that leaves running events untouched. | `backend/api/host.py`, `backend/api/sweep.py` |
| **Per-event spend accounting** | A real per-event cost derived server-side from the token counters every worker already writes — which is what makes the $3 public ceiling something other than a no-op. | `backend/shared/spend.py` |
| **Event volume guardrails** | `dailyMediaCap` (rolling 24 h) and `lifetimeMediaCap` bound how much model work an event can ever cause; `reelCommissionEveryNMedia` refuses a new reel until enough new media has landed. Enforced **in the same transaction as the per-guest rate limit**, and counted on **net-new `clientMediaId`s only** — so an outbox retry can never spend a guest's or an event's budget twice. `None` on every ordinary event: a real host's party is bounded by the guest list and the venue, not by a number. | `backend/schemas/event.py`, `backend/api/uploads.py::_register_batch`, `backend/directors/reel/commission.py` |
| **Hourly orphan sweep** | Stranded stages, face-cluster reconciliation, orphan objects, abandoned upload intents, and the public-event guardrails. | `backend/api/sweep.py` |
| **Chaos injection** | `worker-curate` reads a per-request `ops/chaos` document and can be told to fail its next N calls **without a redeploy** — a real Cloud Tasks backoff and a successful retry, with no `ops/` alert until the budget is genuinely exhausted. Gated to non-production event classes. | `backend/shared/chaos.py` |
| **Tick heartbeat** | `platform/tickPulse`, one document per tick, because autonomy that exists only in Cloud Logging can't be asserted by a test or read by a countdown. | `backend/api/internal.py` |
| **Installable PWA** | Manifest, service worker, and a full icon set (favicon, `apple-touch-icon`, 192/512 maskable) — required for iOS Web Push, and the reason a guest never sees an app store. | `frontend/public/`, `frontend/src/app/layout.tsx` |

---

## 8. Architecture

**An event-driven data plane feeding a goal-driven control plane, with governance as a cross-cutting
rail.** Guest media flows push-based from phones into Cloud Storage, through Eventarc and
rate-limited Cloud Tasks queues, into three perception workers, landing as richly-annotated Firestore
documents whose realtime listeners drive every screen. Above that, two director agents run on **Cloud
Run, inside the Cloud Scheduler tick that already holds the per-event lease** — the guardrails *are*
the product, so they execute in the same process and transaction scope as the lease protecting them.
A single deterministic visibility function — **never an LLM** — decides what the public sees.

Full review document: [`docs/architecture.md`](docs/architecture.md).

### Repository map

```
backend/
  api/            FastAPI surface: uploads, identity/claims/consent, host lifecycle, moderation,
                  reels, membership, push, the Scheduler tick target, the hourly sweep
  intake/         the Eventarc target — EXIF, thumbnails, md5 dedupe, GPS strip, fan-out
  workers/
    curate/       Curator agent (gemini-3.5-flash-lite) + deterministic stage fusion
    face/         Face Indexer — InsightFace ONNX, 512-d embeddings, cluster adoption
    safety/       Guardian agent (Vision SafeSearch hard gate + a Gemini dignity rubric)
    video_prep/   probe, poster, keyframes, proxy
    dlq/          the quarantine consumer
  publisher/      kiosk playlist writer: pure ranking function, per-event leader election
  directors/
    story/        ledger → reason → act, session window, memory, diary, world model, validators
    reel/         select → direct → critic → edl → music → render → publish
  render/         Cloud Run Job entrypoint — ffmpeg + librosa beat grid
  shared/         Firestore/GCS/Tasks/Jobs clients, leases, visibility, coverage, spend, push,
                  faces, stages, chaos, structured logging
  schemas/        Pydantic wire + model-output contracts (one file per boundary)
  services/       thin SDK wrappers: gemini (ADK runner), vision, armor, armor_plugin
frontend/src/
  app/            routes: / · /host · /host/[eventId] · /join · /join/[eventId] · /kiosk/[eventId]
                  · /how-it-works · /events/[eventId]/claim
  components/     host · join · gallery · kiosk · me · consent · claim · judge · atoms
  lib/            API client, Firestore listeners, IndexedDB outbox, upload manager, push,
                  kiosk prefetch, event-time helpers
  design/         spec-12 design tokens — every component reads these, none hard-codes a colour
deploy/           idempotent gcloud scripts: bootstrap, sa, buckets, queues, firestore, eventarc,
                  scheduler, render, up, scale-down, demo-mode
scripts/          seeding (seed_trip = the 5-day trip, seed_global_event = the standing tour event),
                  risk probes, and one smoke script per subsystem
eval/             golden-fixture harness (`make eval`) — 25 fixtures, 169 checks
rules-tests/      Firestore rules emulator matrix (`make rules-test`) — 107 assertions
docs/specs/       the build contract, 01–13, each with a shipped/partial/designed-not-built status
docs/architecture.md   the review document (flows, service inventory, limits)
firestore.rules   deny-by-default, claims-only, no get() joins
```

### State management

- **Firestore is both the system of record and the push channel.** A per-media state machine
  (`awaiting_upload → uploaded → processing → indexed | quarantined`) with **parallel stage flags**,
  so any stage can fail, retry and replay independently. No client anywhere polls: every surface is
  an `onSnapshot` listener.
- **Directors keep tick-to-tick narrative state in a rolling 10-tick session window, compacted by
  deterministic arithmetic inside the ACT step — never by the model summarizing its own history.**
  This is the direct answer to **context rot**: the window has a fixed bound, so the prompt the
  director reasons over **cannot grow with the event's age**
  (`backend/directors/story/session.py`). Long-term host and per-person preferences live in an
  optional Memory Bank scoped `{eventId}:host` / `{eventId}:{personId}` — and **nothing that gates a
  bounty, a payment or who is important is ever read from it**. VIP is policy, not memory.
- **Per-stage verdict blocks are merged by one deterministic function, not by agents contending for
  one document.** `curator`, `faces` and `guardian` each write their own field independently and in
  parallel; `recompute_visibility` is the single place that reads all three and derives `status` and
  `visibility` in one transaction — a ranking/merger step, exactly the shape recommended for
  resolving state written by multiple concurrent workers, built before it was asked for.
- **Client upload state survives app close** in an IndexedDB outbox, and intent is registered in
  Firestore **before** bytes move, so orphans are detectable and consent is never ambiguous.
- **Every id is a ULID** (time-sortable, no Firestore hot keys), every timestamp is UTC, and EXIF
  capture time is stored separately from upload time so a Day-1 batch uploaded on Day 3 sorts
  correctly in a gallery and cannot walk the director's stage pointer backwards.

### Tools properly isolated and scoped for security

- **One least-privilege service account per service** (`deploy/sa.sh`): intake cannot call Gemini;
  the curator cannot write buckets; the perception workers can read the **derived** bucket only, so
  they never see a guest's full-resolution original; the renderer is the only writer to the curated
  bucket and has **no grant on `raw` at all**.
- **Directors run under their own service identity, inside the same transaction/lease scope as the
  guardrails that constrain them** — there is no separate agent-identity layer to audit, because
  there is no gap for one to sit in.
- **Model Armor in two places, for two different reasons.** As an **ADK plugin** in front of every
  director/agent model call (`services/armor_plugin.py` — director, reel, bounty check, world model,
  taste memo), and as an **ingress guard** on every host text surface that can reach a prompt
  (`services/armor.py` — the itinerary paste, the text transcribed out of a PDF, the event profile,
  the stage table). Used **as designed** for prompt-injection and PII — deliberately *not*
  misapplied to photo moderation, whose image screening is Preview and one image per request.
- **Judgment by agents, enforcement by policy.** LLMs write opinions. Only the deterministic,
  transactional `recompute_visibility` writes the `visibility` field, and Firestore security rules
  (custom claims, **no `get()` joins**) enforce it server-side. **No LLM ever gates exposure.**
- **A biometric cannot be protected by a rules exception** — Firestore grants or denies whole
  documents — so face embeddings live in their own `enrollments/{personId}` collection with **no
  `allow` rule at all**, unreadable by every client including the host console.
- **The tick endpoint's access control is the endpoint.** `api` is the one
  `--allow-unauthenticated` service, so `POST /internal/tick` verifies the Cloud Scheduler OIDC
  token **in the handler** against a service-account allowlist plus an audience-host check
  (`backend/shared/oidc.py`).
- **Signed URLs**: 15-minute expiry, with Content-Type **and** Content-Length pinned in the V4
  signature. Minted through IAM `signBlob`, which is why **no service-account key file exists
  anywhere in this project**.
- **Guest media only ever flows through the billed Gemini tier** — free-tier data may train models.

### Failure handling

- **Eventarc at-least-once + a dead-letter queue** → quarantine + a host-console ops alert with a
  replay button.
- **A failure taxonomy, and the two halves are deliberately asymmetric.** *Transient* (429/5xx) →
  3 in-process retries, then Cloud Tasks backoff across 5 attempts, then quarantine — because we
  don't know what the item is, so a human is told. *Permanent* (corrupt file, schema-invalid, model
  refusal) → write the stage's conservative default, complete, and **leave the item in its
  uploader's private album** — because a language model having an opinion should never hide a
  guest's own photo from them.
- **Every handler is idempotent**, with transaction guards keyed on `mediaId` + stage; duplicate
  deliveries are absorbed rather than double-charged.
- **Guardrailed actuation** is the answer to *"how does the system recover if a worker agent loops or
  returns a hallucination?"* — a looping or hallucinating turn is **rejected and logged, never
  applied**, and `make smoke-director --guardrails-only` runs **30 adversarial rows** of exactly
  that (hallucinated `personId`, duplicate target, budget exhaustion, points overflow, illegal
  escalation) with no network and no spend.
- **Chaos-tested without a redeploy**: `ops/chaos` tells `worker-curate` to fail its next N calls,
  producing a real Cloud Tasks backoff and a successful retry, with no ops alert until the attempt
  budget is genuinely exhausted.
- **A Cloud Run service whose job is holding Firestore listeners needs `--no-cpu-throttling`**, not
  just `min-instances 1` — a lesson learned the hard way, and now pinned in `deploy/up.sh`.

---

## 9. The agent fleet (an honest census)

A component earns the word "agent" here only if it makes **context-dependent judgments or plans
actions**. Everything else is deliberately deterministic code — we think calling a script an agent is
agent-washing.

| # | Agent | Kind | Model | Why it's an agent, not a script |
|---|---|---|---|---|
| 1 | **Curator** | perception | `gemini-3.5-flash-lite` | Judges stage / moment / aesthetics / scene setting against an Event Graph and the host's own cultural glossary — no rule table could |
| 2 | **Guardian** | perception | Vision SafeSearch + `flash-lite` | Dignity judgment (ritual tears vs. distress) is irreducibly contextual |
| 3 | **Face Indexer** | *specialized ML worker* | InsightFace ONNX (**no LLM**) | **Honestly labelled**: deterministic embedding math. Calling it an LLM agent would be agent-washing |
| 4 | **Story Director** | director | `gemini-3.7-flash` | A goal ("full coverage"), a continuous observe → reason → act loop, guarded tools, a rolling 10-tick session window and an optional Memory Bank |
| 5 | **Reel Director** | director | `gemini-3.7-flash` | Creative planning: evidence → narrative brief → EDL, tool orchestration (Lyria, Veo, render jobs), and generator+critic self-correction |

Plus one **tool call that is deliberately not called an agent**: the itinerary extractor
(`gemini-3.7-flash`, one-shot structured extraction). It is the product's most impressive single
model call and it still doesn't qualify — it plans nothing and takes no action.

Other deliberate **non-agents**: intake, the kiosk publisher, `recompute_visibility`, the ffmpeg
render job. And the perception agents deliberately **cannot act** — they emit a structured opinion
onto a document and nothing else. That separation *is* the trust architecture: **judgment by agents,
enforcement by policy.**

---

## 10. Google Cloud services — used, and called in code

| Service | Role | Code |
|---|---|---|
| **Cloud Run** (8 services) | `api`, `intake`, `dlq`, `worker-curate`, `worker-face`, `worker-safety`, `worker-video-prep`, `publisher` | `backend/{api,intake,workers,publisher}/`, `deploy/up.sh` |
| **Cloud Run** (1 Job) | ffmpeg reel renders at 8 vCPU, off the request path | `backend/render/main.py`, `deploy/render.sh` |
| **Cloud Storage** | `raw` / `derived` / `curated` buckets; direct-from-phone signed uploads (servers never touch bytes) | `backend/shared/gcs.py`, `deploy/buckets.sh` |
| **Eventarc** | `object.finalized` → intake, with a DLQ on the underlying subscription | `deploy/eventarc.sh` |
| **Cloud Tasks** | 6 queues — **the single throttle** metering Gemini spend (8 dispatches/s), plus a priority lane so every uploader gets one photo on the wall fast under a burst | `backend/shared/tasks.py`, `deploy/queues.sh` |
| **Cloud Scheduler** | `director-tick` (every 2 min, all `live`/`wrapping` events) + `director-tick-demo` (30 s effective via a Cloud Tasks interleave, `protected_demo` only) + the hourly sweep | `deploy/scheduler.sh`, `backend/api/internal.py` |
| **Firestore + native vector search** | System of record, realtime fan-out to every surface, and 512-d face KNN via `findNearest` | `backend/shared/fs.py`, `backend/shared/faces.py` |
| **Firebase Auth + Hosting** | Anonymous guests, magic-link/recovery-code host claims; the PWA and kiosk are a static export | `frontend/src/lib/firebase.ts`, `firebase.json` |
| **Cloud Vision** | SafeSearch hard gate + face quality/joy signals | `backend/services/vision.py` |
| **Model Armor** | ADK plugin in front of every director model call, plus an ingress check on every text-accepting endpoint | `backend/services/{armor_plugin,armor}.py` |
| **Agent Runtime (GEAP)** | **Memory Bank only**, and it ships **inactive**: `AGENT_ENGINE_ID` is empty by default, so the same host free-text is read straight off the event document. The integration is real code on a real API (`VertexAiMemoryBankService`, scoped `{eventId}:host`) and every write is best-effort, holding soft context that gates nothing. The directors run on Cloud Run inside the Scheduler tick, **not** on Agent Runtime — this README says so rather than claiming a component it doesn't use | `backend/directors/story/memory.py` |
| **Cloud Logging** (structured) | One JSON line per stage per item — `event_id`, `media_id`, `stage`, `ms`, verdict — so a saved, event-filtered query is a live view of the pipeline. **No OpenTelemetry spans and no Agent Observability dashboard are claimed:** there is no Agent Runtime deployment to attach one to, and `platform/tickPulse` + `ledger/directorState` are the checkable evidence instead | `backend/shared/log.py`, `backend/shared/pipeline.py` |
| **Secret Manager / IAM / Budgets** | Key handling, one SA per service, cost rails; local auth is ADC, never a key file | `deploy/sa.sh`, `deploy/bootstrap.sh` |

---

## 11. Deliberately NOT used (and why)

| Service | Why not |
|---|---|
| **GKE** | Cloud Run gives identical container semantics with zero cluster ops at this scale |
| **Vertex AI Vector Search** | ~$65+/mo of idle ScaNN for 10k vectors, and flat exact KNN is *more* accurate here. Stated evolution path at ~1M faces |
| **Transcoder API** | Hard cuts only — no transitions, captions or Ken Burns. It cannot make a reel |
| **Model Armor image screening** | Preview, one image per request, and designed for prompt-attack screening rather than photo moderation. We use SafeSearch (GA) + a Gemini dignity rubric instead |
| **Agent Gateway** | Heavy network provisioning; the governance story we actually need is covered without it |
| **Agent Registry / Agent Identity** | The directors run inside the tick that holds the lease, under that service's own identity. Adding a registry would add an audit surface without removing one |
| **Cloud SQL / BigQuery / Memorystore** | No relational, streaming-analytics or hot-cache workload. Firestore is the database *and* the push channel |

---

## 12. Bonus Google AI models — each with a real job

| Model | Job | Code | See it |
|---|---|---|---|
| **Lyria 3** (`lyria-3-clip-preview`) | Composes every reel's soundtrack from the director's music brief; cuts are beat-snapped to its output ($0.04/clip) | `backend/directors/reel/music.py` | Audible at every reel premiere, labelled in-app |
| **Veo 3.1 Fast** (`veo-3.1-fast-generate-001` via Vertex) | An 8-second cinematic opener, image-to-video from the top portrait, concatenated in post-render | `backend/directors/reel/opener.py` | Prepended to the hero reel |
| **Gemma 4** (`gemma-4-26b-a4b-it`) | A private per-person taste memo from album ❤ reactions — a real, zero-cost job deliberately isolated **off the critical and money paths** | `backend/directors/story/taste.py` | `people/{personId}/private/profile.tasteMemo` — a deny-all subcollection, so the memo is never readable by any client. The Memory Bank mirror is best-effort and off unless `AGENT_ENGINE_ID` is set |

---

## 13. Trust architecture (consent, dignity, guardrails)

- **3-ring consent**: self-only / event-pool (the default) / public (a per-batch opt-in) — retroactive
  within seconds, with a subject veto, and a delete-my-data path that deletes face documents, the
  512-d embedding, the private profile and the push registration, and tombstones the person's uploads.
- **Selfie enrolment is an explicit biometric consent artifact.** Identity math runs in *our* ONNX
  model and *our* database. **Gemini is never used for identity.**
- **Impersonation guard: no face match grants anything, ever.** Every enrolment and re-claim is
  written with `claimApproved: False` and held for host review; the grant lives only in the approve
  branch of `POST /claims/{claimId}/review`, and a decision can be reversed afterwards. This closed a
  real defect found mid-build — a bride's photo lifted off the public gallery and submitted as a
  selfie now returns `pending_host_approval` at 0.982 similarity with
  `holdReason=protected_person`, zero faces claimed, and a 403 on her album. The cost of that
  strictness is [limit #14](#18-honest-limits-designed-not-built), stated rather than traded away.
- **Guardian layers a hard gate under a judgment call**: Vision SafeSearch short-circuits the model
  call **entirely** on explicit content (no explicit image is ever described in prose to a language
  model, and the call is not paid for), and a Gemini dignity rubric returns *observations plus a
  proposed verdict* that a pure function combines with the SafeSearch floor, a deterministic
  `minor_prominent → host_review` rule, and the host's dials **as a ceiling**. Anything ambiguous
  routes to the human host, never to the kiosk.
- **The event boundary is real.** `isMember(eventId)` reads a `members` array claim minted by
  `POST /v1/events/{eventId}/join`; `host` is a `hosts` **array** (a second event used to silently
  revoke the first). Cross-event reads are denied in both directions, verified live.

---

## 14. Scale & cost (production readiness)

Designed for **500 concurrent guests / 5,000 photos / burst uploads**:

- Direct-to-GCS signed uploads — **servers never touch bytes**.
- Cloud Tasks backpressure calibrated to Gemini spend-tier caps (8 dispatches/s against Tier-1's
  rolling cap) — one throttle, in one place, for the whole fleet's model spend.
- Idempotent replays, a DLQ, index exemptions on sequential timestamps, `limit()`-bounded listeners.
- Zero-token idle ticks, so an overnight gap on a five-day event costs nothing.

**A full 5,000-photo wedding costs $20–40 end to end** — classification under $5 (thumbnails +
`flash-lite` at low media resolution), 10 reels ≈ $2, a Veo opener $0.80, everything else pennies.
*A season of weddings costs less than one printed album.*

Measured per-stage: warm Curator **1.8–1.9 s**, warm Guardian **1.5 s**, ~1,500–1,850 input tokens
per perception stage. Full detail and honest limits: [`docs/architecture.md`](docs/architecture.md).

---

## 15. Spin-up (two tiers)

### Tier 0 — no cloud account needed

Clone the repo and run **164 real assertions** without a GCP project, a key, or a cent of spend:

```bash
make rules-test                                     # 107 Firestore rules assertions, 8 boundary groups,
                                                    #     11 personas (Firebase emulator + Python, no Node)
python scripts/smoke_safety.py   --gate-only        #  15 deterministic safety-gate decisions, no network
python scripts/smoke_director.py --guardrails-only  #  30 adversarial director-guardrail rows, no network
python scripts/smoke_reel.py     --offline          #  12 reel-pipeline claims, no ffmpeg, no network
```

`make eval` runs the 25-golden-fixture harness (169 checks), but it re-fetches **live** Firestore
documents, so it needs a deployed project (Tier 1) — it is not credential-free, unlike the four
checks above.

### Tier 1 — full deploy, reproducible from a clean GCP project

```bash
# Prereqs: gcloud CLI, Python 3.11, Node 20, a GCP project with billing enabled
git clone https://github.com/Shantanu-00/showrunner && cd showrunner
cp .env.example .env                      # fill: project id, region, Gemini API key

./deploy/bootstrap.sh                     # enable APIs, create buckets/queues/indexes/SAs/Armor template
./deploy/up.sh                            # deploy 8 services + the render job, Eventarc, Scheduler, rules

python backend/seed.py --event demo       # the eval/dev fixture event, seeded THROUGH the real pipeline
make seed-trip                            # ...and the 5-day Japan trip (multi-day, generic timeline)
python scripts/seed_global_event.py       # ...and the always-live protected_demo global demo

python scripts/smoke_upload.py            # 1 photo end-to-end; asserts kiosk-eligible, fast
make check                                # tsc --noEmit && next build
make deploy-hosting                       # static export → Firebase Hosting
```

**One manual step the scripts cannot do for you: the Web Push VAPID key.** Firebase console →
Project settings → Cloud Messaging → *Web Push certificates* → Generate key pair, then put the
**public** key in `NEXT_PUBLIC_FIREBASE_VAPID_KEY` before `make deploy-hosting`. There is no gcloud
or REST surface for generating it, which is why `deploy/bootstrap.sh` writes every other Firebase
value and not this one. Leave it empty and push simply does not appear — `pushSupport()` reports
`not-configured`, the opt-in row renders nothing, and **every other surface is unaffected**.

Everything in `deploy/` is **idempotent**: re-running `up.sh` updates in place rather than
duplicating. Full walkthrough with expected output:
[`docs/specs/09-infrastructure-and-demo.md`](docs/specs/09-infrastructure-and-demo.md).

---

## 16. What you can verify yourself

| Command | What it proves | Needs a cloud account? |
|---|---|---|
| `make rules-test` | 107 assertions × 8 boundary groups × 11 personas (stranger, member, subject, uploader, host, another event's host, banned, platform admin…). Includes *"a biometric is unreadable by every client, including the host console."* | No |
| `python scripts/smoke_safety.py --gate-only` | 15 rows: the SafeSearch hard gate overrides a permissive model verdict, `minor_prominent` can never be public, a dial can clamp but never release, a refusal defaults to `host_review` | No |
| `python scripts/smoke_director.py --guardrails-only` | 30 rows: hallucinated `personId` rejected, duplicate targets collapsed within a plan *and* across ticks, the 2-per-tick budget spent but not exceeded, points always in `[50, 300]`, advance requires confidence **and** the timetable | No |
| `python scripts/smoke_reel.py --offline` | 12 rows: persona divergence, flat-storyboard rejection, the linter, every cut landing on the beat grid (measured error 0.000 ms against an 80 ms bound), no face crossing the frame edge, a too-wide face line-up **fitted rather than cropped through somebody's head**, the filtergraph offsets, and a prompt of stored evidence with no pixels and no URIs in it | No |
| `make eval` | 25 golden fixtures, 169 checks, re-fetched live from Firestore | Yes |
| `make smoke` / `smoke-faces` / `smoke-safety` / `smoke-autonomy` / `smoke-dlq` | The live paths: a real signed-URL upload through Eventarc; a selfie filling an album and a VIP match being held; the Scheduler firing unprompted and the wall updating; and budget exhaustion → quarantine → replay → recovery | Yes |
| `make check` | `tsc --noEmit` + `next build` — a static frontend check that spends nothing | No |

**Last verified live run:** all 8 services HTTP 200, **eval 25/25 fixtures and 169/169 checks**,
rules **107/107**, zero ERROR-severity logs across the fleet, and the tick heartbeat live.

---

## 17. Challenges, findings & learnings

*What actually fought back, and what we did about it. A dated friction log was kept from day one; the
load-bearing entries — the ones that changed a design decision or cost real money — are reproduced
here in full, each next to the code that carries the fix.*

- **An inline image costs ~1,055 input tokens at the default media resolution — regardless of the
  render size you send.** That put our per-photo prompt 57% over budget while we were shrinking
  thumbnails to fix it. `MEDIA_RESOLUTION_LOW` (~260 tokens) fixed it with **no measured quality
  loss**. Corollary worth knowing: a 12-frame keyframe grid at default resolution is ~12,600 tokens,
  not the ~3,100 a naive estimate assumes.
- **Cloud Tasks refuses to attach an OIDC token to an `http://` target — and `request.url.scheme` is
  always `http` behind Cloud Run's TLS termination.** So our 30-second demo interleave silently
  failed with a 400 while the tick itself still returned 200. Two failures that cancel into a green
  status code are the worst kind.
- **A Cloud Run service that holds Firestore listeners needs `--no-cpu-throttling`, not just
  `min-instances 1`.** With CPU allocated only during requests, background threads freeze between
  requests and the wall stops updating **with no error anywhere**.
- **Tailwind v4 silently drops an arbitrary value it can't type-infer.**
  `font-[var(--font-display)]` compiled to `--tw-font-weight:var(--font-display)` — v4 guessed
  `font-weight` and never emitted a `font-family`, so 34 call sites across 24 files rendered no
  typeface at all, with no build warning. Fixed with v4's data-type hint,
  `font-[family-name:var(--font-display)]`.
- **Firestore listeners return `Timestamp` objects, and a typed cast hides it.** Our TypeScript
  interfaces describe the *API's* JSON (`createdAt?: string`), which is wrong for a raw snapshot:
  `.localeCompare` on a `Timestamp` threw and took a whole page down, while `new Date(timestamp)`
  silently returned `NaN` — and because `NaN` is falsy, a guard swallowed it and rendered a
  permanently-full countdown ring instead of an error. Fixed by normalizing at the listener boundary.
- **The kiosk's slot timer was cancelled by its own unchanged data.** An effect depended on the whole
  `playlist` object, which is a new reference on every snapshot — including the deliberate
  `checkedAt`-only touch the publisher makes on an unchanged rebuild. Every snapshot cancelled the
  pending advance timeout before it could fire, so on a 30-second tick cadence the kiosk parked on
  slot 0 forever with no error anywhere. Fixed by making the reader's change-detection match the
  writer's fingerprint discipline.
- **A one-line retry-condition inversion silently failed most reel commissions.** The pipeline broke
  out of its retry loop on `attempt == 2 or len(shots) < MIN`, so the storyboard that most needed a
  second attempt — one the linter had just cut below the shot floor — was the one guaranteed not to
  get it. 8 of 9 test commissions failed **after paying for a model call** before this was caught.
- **Reseeding inside one clock hour resurrected a dedupe entry for a deleted document.** The reset
  cleared media/people/enrollments but not the per-event md5 register, so byte-identical cached
  portraits hit the duplicate branch and the reseeded item was silently truncated to a single stage.
  Idempotency is only as good as the *completeness* of what you reset.
- **The biggest architectural lesson**: three real production bugs (a CORS preflight missing `PUT`, a
  client reading a pre-array `host` claim, and a missing token refresh after event creation) were
  each **invisible to every automated check we own**, because every one of them is server-side
  Python. Naming that gap is worth more than pretending it isn't there — see
  [§18](#18-honest-limits-designed-not-built).

---

## 18. Honest limits (designed, not built)

Every claim above is checkable in the repository. These are the things that are **not** true yet,
listed so nobody has to discover them. The project's own rule is that an unbuilt row gets **deleted
or named, never softened**.

| # | Limit | Detail |
|---|---|---|
| 1 | **No retention or purge** | Nothing deletes a wrapped event's data. An earlier draft of this README and two specs claimed a 30-day post-`wrapped` sweep; there is no purge and no sweep, and the claim was **retracted in writing** (spec 09 §2, spec 11 §1.3) rather than quietly deleted. What *does* work is per-**subject** and immediate: `DELETE /v1/events/{id}/people/me` really deletes a person's face documents, their 512-d embedding, their private profile and their push registration, and tombstones their uploads. |
| 2 | **The public-event TTL is a flat 60 minutes** | …and ignores the event's own declared date range. See [the callout in §5](#5-it-runs-for-the-whole-event-long-running), including workarounds and the correct fix. |
| 3 | **`internal_dev` events have no TTL sweep at all** | `_sweep_guardrails` returns early on anything that isn't `class == 'public'`, so the 24-hour dev safety net is unimplemented and every throwaway dev event stays live and ticked forever. Found during deploy verification; named rather than fixed under time pressure. |
| 4 | **The AI-proposed kiosk theme is usually inert** | The extraction prompt asks the model for one of 13 evocative palette names (`golden_hour`, `candlelight`, …), but the kiosk knows exactly 8 (`gold`, `violet`, `crimson`, `ocean`, `forest`, `neon`, `slate`, `sunrise`) and `KioskShow.tsx::resolveStageTheme` drops anything else. It **degrades correctly** — the wall keeps the default palette rather than breaking — but until a host picks a theme from the stage editor's dropdown, the model's suggestion does nothing. The fix is one vocabulary, in one place. |
| 5 | **`vipTopology` is stored and read by nothing** | The `pyramid`/`flat` field has no runtime consumer; the wizard's *Equal Coverage* checkbox is what actually sets tiers, client-side, per person. Worth knowing before a demo: people added with that checkbox off land at tier 3, and the ledger only considers tier ≤ 2 — so a per-person coverage gap will not fire for them. |
| 6 | **No "scenery is missing" gap kind** | The gap kinds are `moment`, `vip`, `vip_thin` and `group`. `sceneSetting` is captured per photo and does drive the kiosk's on-topic demotion, but nothing computes *"this event is short of establishing shots."* The workaround that does work is a host-declared required moment (the trip seed uses exactly that). |
| 7 | **Bulk photo export does not exist** | The recap film is downloadable; *"give me all my photos as a zip"* — which is what most people actually want at the end of a trip — is not built. The Google Photos batch-upload surface is confirmed reachable; the OAuth consent-screen click-through is the remaining step. |
| 8 | **The kiosk has no quiet hours** | It runs continuously from Go Live to `wrapped`. Overnight on a multi-day event the active stage resolves to `none`, the theme clears, and the wall keeps cycling the best of what exists. That is a reasonable screensaver, not a designed sleep state. |
| 9 | **Reel v2 supersession is schema-only** | `version`, `previousVersionId` and `ReelStatus.SUPERSEDED` are live; the debounced re-edit-on-a-better-photo trigger is specced and honestly not wired. |
| 10 | **`renders-queue` is configured and unused** | Cloud Tasks cannot start a Cloud Run Job (Tasks speaks HTTP; a Job has no URL), so the reel pipeline starts through the Run Admin API instead, and the real invariant — one active render *per persona, per event* — lives in `directors/reel/store.py::in_flight_of_persona`. Dead config, named here rather than left to be found. |
| 11 | **The Curator's prompt is 19% over its own pinned rail** | ~1,845 input tokens per photo against a documented 1,548. The Cloud Tasks queue rates were calibrated against the rail, so this needs a decision — trim the prompt, or re-derive the rates — and it is documented rather than silently accepted. |
| 12 | **Every automated check is server-side** | `rules-test`, `eval` and the smoke scripts are Python, so they cannot see browser-path failures. Three real production bugs were invisible to all of them. A Playwright pass against a live deployment is the gap-filler and is **not built**. |
| 13 | **Residual read exposure, by design** | Documented in `firestore.rules`'s own header: `kiosk/playlist` is world-readable (a TV in a venue has no auth session), and it resolves to nothing useful for a non-member because every collection it points into is member-gated. And a member reads whole `media` documents, which carry `albumOf`, `subjectVetoes` and the Guardian verdict. |
| 14 | **A visitor who enrols on the standing demo waits for an approval nobody is watching** | The impersonation guard holds **every** enrolment for host review (`claimApproved: False` until the host says yes) — which is correct at a real event and awkward on `global_demo`, whose host is not sitting at a console. An `autoPromoteEnrollees` flag exists and is deliberately left **off**, because it is the last class-conditional branch in the system. The honest fix is a host-settable auto-approve any host could also set, with copy naming what it gives up. Everything else on the tour works without enrolling. |
| 15 | **The Flight Deck was cut entirely** | Spec 10's live pipeline visualizer has zero code and is claimed nowhere judge-facing. Named here so the spec's existence isn't mistaken for a feature. |

---

## 19. Disclosures

- **AI coding assistants were used throughout**, as expressly permitted by the rules. All
  architecture decisions, specifications and reviews are the entrant's.
- **InsightFace pretrained weights are licensed for non-commercial research** — appropriate for this
  hackathon. The documented production swap is AuraFace / OpenCV SFace (permissive).
- **Host auth uses magic links and recovery codes** for demo friction-lessness; the production swap
  is Google Sign-In, with the claims structure unchanged (and Google sign-in already exists as an
  optional upgrade).
- **Demo dataset**: only owned/consented photographs and AI-generated cast members. **All music is
  Lyria-generated.** No third-party trademarks, logos or licensed music appear anywhere.
- **Built entirely within the Submission Period** (August 2026). No pre-existing code incorporated.
- **The architecture diagram** is generated from checked-in HTML sources
  (`docs/assets/architecture-overview.html` and `architecture-lld.html` →
  `scripts/render_architecture.py`), so it is a reproducible artifact rather than a one-off export.
  Page 1 of the PDF and the image above are rasterized from the same PDF page, and every box on it
  maps to something actually deployed — what is specified but not built is listed separately, on
  page 6 of the PDF.

---

## License

[MIT](LICENSE) · Built solo by **Shantanu** ([`Shantanu-00`](https://github.com/Shantanu-00)) for the
All Things Agentic Hackathon · [demo video]({{VIDEO_URL}}) · [build log]({{BLOG_URL}}) ·
[#AllThingsAgenticHackathon]({{SOCIAL_URL}})
