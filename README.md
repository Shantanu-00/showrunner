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

- `backend/` — FastAPI services, ADK agents, workers (see `docs/specs/09` for infra detail)
- `frontend/` — Next.js PWA (join / host / kiosk / gallery / reels / Flight Deck)
- `deploy/` — idempotent `gcloud` setup + deploy scripts
- `scripts/` — seeding + risk-probe scripts
- `eval/` — golden-fixture evaluation harness
- `rules-tests/` — Firestore security rules emulator matrix
- `docs/specs/` — the build contract
- `docs/architecture.md` — architecture overview

## License

MIT (recommended; finalize before public release).
