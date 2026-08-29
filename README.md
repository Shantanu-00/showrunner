# Showrunner — the autonomous media director for live events

Guests scan a QR and upload photos. A fleet of agents classifies every shot, indexes faces,
screens for dignity and safety, runs the venue's big screen, detects coverage gaps against the
timeline, dispatches photo bounties to guests' phones, and directs beat-synced highlight reels
with AI-composed soundtracks — while the event is happening.

Built for the All Things Agentic Hackathon (Taskmaster category). This README is a stub during
the build; the full judge-facing version accretes from B2 onward (see `docs/devpost/README-PLAN.md`,
local-only).

## Status

Actively building — see `docs/architecture.md` and `docs/specs/` for the design contract.

## Repository layout

```
backend/
  api/        signed URLs, identity/claim flow, host lifecycle, media review, reel commissions
  intake/     Eventarc target — EXIF, thumbs, dedupe, media doc, fan-out
  workers/    curate (Curator) · face (Face Indexer) · safety (Guardian) · dlq (quarantine consumer)
  publisher/  kiosk playlist writer, per-event leader election
  directors/  story/ (Story Director: ledger→reason→act) · reel/ (Reel Director: select→direct→critic→edl)
  render/     Cloud Run Job entrypoint — ffmpeg + librosa beat grid
  shared/     settings, Firestore/GCS/Tasks clients, leases, pipeline lifecycle, visibility
  schemas/    Pydantic contracts shared across every service
  services/   thin SDK wrappers (Gemini, Vision, Model Armor)
frontend/src/
  app/        join, host, kiosk, judge, events/[id]/claim routes
  components/ per-surface UI (join, kiosk, host, judge, gallery, me)
  lib/        firebase, outbox, api client, Firestore listeners, types mirroring backend/schemas
  design/     tokens.css + fonts — spec 12 design system
deploy/       idempotent gcloud scripts (bootstrap, up, scale-down, judge-mode, scheduler, ...)
scripts/      seeding, risk probes, smoke tests
eval/         golden-fixture harness (make eval)
rules-tests/  Firestore rules emulator matrix (make rules-test)
docs/specs/   the build contract (01-12), with a shipped/partial/designed-not-built status column
docs/architecture.md — architecture overview
```

## License

MIT (recommended; finalize before public release).
