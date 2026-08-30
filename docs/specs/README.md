# Showrunner — Implementation Specs

Implementation-ready specifications. Each file is self-contained: data model, flows, API contracts, edge cases, and acceptance criteria. A coding agent should implement them **in numeric order** — each spec only depends on lower-numbered ones. **Exception: spec 11 conceptually extends spec 08 and should be read alongside it** — specs 03/04/05/06 each carry a small consumption edit that cites spec 11, so build spec 08 and spec 11 together before those.

Strategic context lives in `../../PLAN.md`. Verified platform facts + source URLs live in `../research/google-stack-research-2026-08-24.md`. When a spec and PLAN.md disagree, **specs win** (they are newer and more detailed).

**Status column** (updated 2026-08-30): `shipped` = live and verified against the deployed
project; `partial` = the core mechanism is live, with named pieces of the spec's scope deliberately cut
or still unverified; `designed-not-built` = the spec exists, no code implements it, and it is not
claimed anywhere. This reflects the real repo, not the spec's own aspirational scope.

Everything named below as "built this session" is code-complete and offline-verified (every decision
table green, every service imports, `tsc`/`next build` clean) but **not yet deployed or verified live**
— no `deploy/up.sh` has run since before this session's work landed. Treat the whole status table as
provisional on that deploy; see `docs/context/HANDOFF.md` §9 for the specific open items.

| Spec | Status | Covers |
|---|---|---|
| `01-upload-and-ingestion.md` | shipped | Client upload outbox (survives app close), signed URLs, video resumable uploads, GCS layout, Eventarc intake, idempotency |
| `02-identity-consent-privacy.md` | shipped | Anonymous identity, rescans/second sessions, magic-link album recovery, selfie re-claim, the 3-ring consent model, deletion, Google Photos export |
| `03-processing-pipeline.md` | partial | Push-based pipeline, per-media state machine, photo + video processing chains, Curator/Face/Guardian agent contracts, stage fusion. **Live and verified:** the photo chain end to end. **Built this session, not yet deployed:** the full video chain (`worker-video-prep` — probe, poster, keyframes, proxy, fan-out); the Curator's `sceneSetting` field (the world model's input, spec 03 §5.1) |
| `04-galleries-and-kiosk.md` | partial | Visibility computation (the one function that decides public vs private), gallery ranking, private albums, kiosk playlist direction. **Live:** the core mechanism. **Built this session, not yet deployed:** the host review queue (a listing endpoint + console panel — every conservative default previously had no reachable escape hatch); the `onTopic` ranking term (a photo's exposure never changes, only its kiosk rank); the kiosk laptop-viewport layout and rewind fixes |
| `05-story-director.md` | partial | Event/stage monitoring control loop, coverage ledger, bounty lifecycle, stage-drift detection. **Live:** the core loop. **Built this session, not yet deployed:** the hourly orphan-sweep (`/internal/sweep` — stranded stages, face-cluster reconciliation, orphan objects, abandoned intents); the world model (a distilled venue paragraph the director's prompt reads for siting, generated from the same `sceneSetting` counts `onTopic` reads — see spec 03 §5.1) |
| `06-reel-director.md` | partial | Reel commissions, narrative brief → EDL, anti-generic design (generator+critic), Lyria + beat sync, render jobs, versioning & late-arrival policy. **Live:** SELECT→DIRECT→CRITIC→EDL→RENDER→PUBLISH, one published reel verified on `judge_demo`. **Not built:** v2 supersession, collages, the `main_character` persona |
| `07-personalization-memory.md` | partial | Swipe (keep/hide) interface, per-person taste profiles, Memory Bank usage, personalized ranking and reels. **Live:** the cheap path — a ❤ reaction → affinity vector → Gemma 4 memo → Memory Bank write, verified offline. **Not built:** the swipe deck itself (replaced by the reaction path); the live Memory Bank write is unverified against real infrastructure |
| `08-host-console-and-lifecycle.md` | partial | Host auth (magic link + claim), the event status master switch (draft→live→paused→wrapping→wrapped), creation wizard with timeline review, console tabs, panic/kill switches. **Live:** lifecycle machine, wizard, itinerary parse, wrap report, Freeze Public, bounty banner/leaderboard. **Built this session, not yet deployed:** the review-queue UI (was endpoint-only; now a full console panel — see spec 04); a `platformAdmin` recovery-code escape hatch for a host who has lost every device. **Not built:** the coverage heat-grid, the People tab |
| `09-infrastructure-and-demo.md` | shipped | Exact service/queue/index/rules configuration values, secrets & least-privilege SAs, teardown/up scripts, demo mode + warm-up runbook |
| `10-pipeline-visualizer.md` | designed-not-built | Flight Deck: the live architecture-with-real-traffic page that carries the demo (chip flow, stage latencies, cost ticker, director feed). Cut entirely (S12) — zero code, not claimed anywhere judge-facing |
| `11-event-onboarding-and-cultural-profiles.md` | partial | Concurrent-live-event hard cap; Event Type Profiles (host-declared cultural/sensitivity dials); VIP tiering + topology + the audited "feature this person" backdoor; Memory Bank scoping mandate. Extends spec 08; consumed by specs 03/04/05/06. **Live:** the capacity cap + kill switch (`platform/liveEventCount`, `publicCreationEnabled`), VIP tiering, cultural profiles. **Built this session, not yet deployed:** the public-event $3 cost ceiling and 60-minute auto-wrap TTL, enforced by the sweep (spec 05) reading a real per-event spend derived server-side from the token counters every worker already writes (`shared/spend.py`) — this field previously had no writer at all, so the ceiling was a permanent no-op regardless of spend |
| `12-frontend-design.md` | shipped | Design system ("Grand Ballroom at Midnight"): tokens, event-adaptive themes, motion language, per-surface visual contracts (kiosk show, bounty choreography, consent ritual, producer console), accessibility/perf budget. **Read before building any frontend surface** — the Best Multimodal UX artifact |
| `13-generic-events-and-multiday.md` | partial | The pivot to itinerary-led generic events (canonical demo: a 5-day group trip): event date range + derived day semantics, dated paste/PDF/screenshot itinerary parse, one active-stage resolver, gap lifecycle + idle ticks + evidence-driven stage advance, group coverage histogram, targeted capture tasks ("assignment never changes pay"), host participant enrollment, the `event_recap` wrap film + Event Diary. Extends 03/04/05/06/08/11. **In build:** §1–§4 code-complete, §5–§8 in progress |

## Global conventions (all specs assume these)

- **IDs:** client-generated **ULIDs** for media (`mediaId`), server ULIDs elsewhere. ULIDs sort by time and avoid Firestore hot-key issues.
- **Time:** all timestamps UTC in Firestore; EXIF capture time stored separately from upload time; galleries order by capture time.
- **Idempotency:** every worker is safe to re-run. Keyed on `mediaId` + stage name. Eventarc/Tasks deliver at-least-once — never assume exactly-once.
- **Firestore paths:** everything event-scoped: `events/{eventId}/...` subcollections (see spec 03 §1 for full tree).
- **Buckets:** `raw` (originals, guest-writable via signed URL only), `derived` (thumbs/proxies/posters — **separate bucket so Eventarc never retriggers**), `curated` (reels, collages).
- **Backend:** Python 3.11, FastAPI, `google-adk` v2, `google-genai` SDK. Pin `librosa<1.0`.
- **Models:** classification `gemini-3.5-flash-lite`; director reasoning `gemini-3.7-flash`; music `lyria-3-clip-preview`; all structured outputs via Pydantic JSON schema.
- **Frontend:** Next.js App Router PWA; Firebase JS SDK (anonymous auth + Firestore listeners); no client polling anywhere.
- **Security:** guests can only read what Firestore security rules expose (see spec 04 §1); all writes that matter go through the API or workers, never direct client writes except `reactions` and upload-intent (validated by rules).
