# Showrunner — Implementation Specs

Implementation-ready specifications. Each file is self-contained: data model, flows, API contracts, edge cases, and acceptance criteria. A coding agent should implement them **in numeric order** — each spec only depends on lower-numbered ones. **Exception: spec 11 conceptually extends spec 08 and should be read alongside it** — specs 03/04/05/06 each carry a small consumption edit that cites spec 11, so build spec 08 and spec 11 together before those.

Strategic context lives in `../../PLAN.md`. Verified platform facts + source URLs live in `../research/google-stack-research-2026-08-24.md`. When a spec and PLAN.md disagree, **specs win** (they are newer and more detailed).

| Spec | Covers |
|---|---|
| `01-upload-and-ingestion.md` | Client upload outbox (survives app close), signed URLs, video resumable uploads, GCS layout, Eventarc intake, idempotency |
| `02-identity-consent-privacy.md` | Anonymous identity, rescans/second sessions, magic-link album recovery, selfie re-claim, the 3-ring consent model, deletion, Google Photos export |
| `03-processing-pipeline.md` | Push-based pipeline, per-media state machine, photo + video processing chains, Curator/Face/Guardian agent contracts, stage fusion |
| `04-galleries-and-kiosk.md` | Visibility computation (the one function that decides public vs private), gallery ranking, private albums, kiosk playlist direction |
| `05-story-director.md` | Event/stage monitoring control loop, coverage ledger, bounty lifecycle, stage-drift detection |
| `06-reel-director.md` | Reel commissions, narrative brief → EDL, anti-generic design (generator+critic), Lyria + beat sync, render jobs, versioning & late-arrival policy |
| `07-personalization-memory.md` | Swipe (keep/hide) interface, per-person taste profiles, Memory Bank usage, personalized ranking and reels |
| `08-host-console-and-lifecycle.md` | Host auth (magic link + claim), the event status master switch (draft→live→paused→wrapping→wrapped), creation wizard with timeline review, console tabs, panic/kill switches |
| `09-infrastructure-and-demo.md` | Exact service/queue/index/rules configuration values, secrets & least-privilege SAs, teardown/up scripts, demo mode + warm-up runbook |
| `10-pipeline-visualizer.md` | Flight Deck: the live architecture-with-real-traffic page that carries the demo (chip flow, stage latencies, cost ticker, director feed) |
| `11-event-onboarding-and-cultural-profiles.md` | Concurrent-live-event hard cap; Event Type Profiles (host-declared cultural/sensitivity dials); VIP tiering + topology + the audited "feature this person" backdoor; Memory Bank scoping mandate. Extends spec 08; consumed by specs 03/04/05/06 |
| `12-frontend-design.md` | Design system ("Grand Ballroom at Midnight"): tokens, event-adaptive themes, motion language, per-surface visual contracts (kiosk show, bounty choreography, consent ritual, producer console, Flight Deck), accessibility/perf budget. **Read before building any frontend surface** — the Best Multimodal UX artifact |

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
