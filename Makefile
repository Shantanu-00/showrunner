.PHONY: up down dev-event smoke smoke-faces smoke-safety smoke-autonomy smoke-director smoke-reel \
        smoke-dlq check \
        seed seed-trip eval demo-reset rules-test \
        deploy-api deploy-intake deploy-dlq deploy-curate deploy-face deploy-safety \
        deploy-video-prep deploy-render deploy-publisher deploy-hosting

REGION := us-central1
PY := python

up:
	./deploy/up.sh

down:
	./deploy/scale-down.sh

# Creates an internal_dev event and prints its id (spec 11 §1.1 — class is server-assigned).
dev-event:
	$(PY) scripts/dev_event.py

# Real anonymous sign-in → signed URL → GCS → Eventarc → intake. Set SMOKE_EVENT_ID first.
smoke:
	$(PY) scripts/smoke_upload.py

# Face Indexer + claim flow: selfie enrollment fills an album; a VIP-matching selfie holds for
# host review and the review endpoint approves it. Needs worker-face deployed.
smoke-faces:
	$(PY) scripts/smoke_faces.py

# Guardian + the first photo to reach status=indexed + host override + surgical replay.
# `--gate-only` runs just the deterministic decision table: no uploads, no spend.
smoke-safety:
	$(PY) scripts/smoke_safety.py

# Wipes the demo event's people/media/ops, then re-generates the AI cast (cached) and re-uploads
# the ~25 golden fixtures through the real pipeline. Re-runnable: always starts from a clean event.
# The autonomy spine: Cloud Scheduler fires unprompted, the tick takes and releases its lease, the
# publisher puts a just-uploaded photo on the wall. `--program-only` runs the hero-score and
# diversity table with no network and no spend.
smoke-autonomy:
	$(PY) scripts/smoke_autonomy.py

# The Story Director: a coverage gap is noticed and a crowd is asked to fix it, a submission is judged
# and paid exactly once, an expired bounty becomes an admitted gap. `--guardrails-only` runs spec 05
# §1's whole guardrail set as a decision table: no network, no Firestore, no spend.
smoke-director:
	$(PY) scripts/smoke_director.py

# The Reel Director: a commissioned reel is directed, scored by Lyria, beat-snapped, rendered and
# premiered on the wall. `--offline` runs every deterministic claim in spec 06 §8 — persona divergence,
# the flat-storyboard rejection, the linter, cuts on beats, no face crossing the frame edge, the
# filtergraph offsets — with no network, no ffmpeg and no spend.
smoke-reel:
	$(PY) scripts/smoke_reel.py

# Budget exhaustion end to end: injects enough failures to exhaust MAX_STAGE_ATTEMPTS, confirms
# `quarantined` + an `ops/` alert at error severity, then replays and confirms recovery. The
# `--chaos` flag on smoke_upload/smoke_safety tests retries *winning*; this is the one script that
# tests them losing. Needs a live deployment and ~5-7 min against real Cloud Tasks backoff.
smoke-dlq:
	$(PY) scripts/smoke_dlq.py

# No unit tests in this project (CLAUDE.md) — but a static/build check that never touches Firestore
# or spends a cent is exactly what `tsc`/`next build` already are, so there is no reason for a
# frontend regression to be caught only by a human staring at a deploy. Run before `deploy-hosting`.
check:
	cd frontend && npx tsc --noEmit && npm run build

seed:
	$(PY) backend/seed.py --event demo

# Sibling of `seed`: the generic-timeline demo — a 5-day Japan group trip at stable id
# `japan_trip_2026`, multi-day stages, flat-topology cast, today (Day 4) left with a live
# group-coverage gap for the Story Director to notice.
seed-trip:
	$(PY) scripts/seed_trip.py

# Wipes the demo event without reseeding — the video plan's "make demo-reset" dependency
# (HANDOFF §7b): every retake after the first needs to run on clean state.
demo-reset:
	$(PY) backend/seed.py --event demo --reset-only

eval:
	$(PY) eval/run_eval.py

rules-test:
	firebase emulators:exec --only firestore "$(PY) rules-tests/run_matrix.py"

# api / intake / dlq / worker-curate share one image; $$SERVICE picks the app at runtime
# (backend/main.py).
# --update-env-vars (not --set-env-vars) so a quick redeploy keeps the config up.sh applied.
deploy-api:
	gcloud run deploy api --source backend --region $(REGION) --update-env-vars SERVICE=api

deploy-intake:
	gcloud run deploy intake --source backend --region $(REGION) --update-env-vars SERVICE=intake

deploy-dlq:
	gcloud run deploy dlq --source backend --region $(REGION) --update-env-vars SERVICE=dlq

deploy-curate:
	gcloud run deploy worker-curate --source backend --region $(REGION) \
	  --update-env-vars SERVICE=worker-curate

# B2 workers: heavier dependency sets, hence their own Dockerfiles. worker-face cannot use
# `--source` (it would pick up backend/Dockerfile, the wrong one) — build explicitly with the
# alternate Dockerfile via backend/docker/cloudbuild.face.yaml, then deploy that image.
deploy-face:
	$(eval FACE_IMAGE := $(REGION)-docker.pkg.dev/$(shell gcloud config get-value project)/showrunner/worker-face:$(shell git rev-parse --short HEAD))
	gcloud builds submit backend --config backend/docker/cloudbuild.face.yaml \
	  --substitutions _IMAGE=$(FACE_IMAGE)
	gcloud run deploy worker-face --image $(FACE_IMAGE) --region $(REGION) \
	  --update-env-vars SERVICE=worker-face

deploy-safety:
	gcloud run deploy worker-safety --source backend --region $(REGION) \
	  --update-env-vars SERVICE=worker-safety

# Same shape as deploy-face and for the same reason: ffmpeg/ffprobe do not ride the common image,
# so `--source` would build backend/Dockerfile — which has no ffmpeg and whose main.py raises on
# SERVICE=worker-video-prep, producing a revision that never becomes ready rather than an error.
deploy-video-prep:
	$(eval VIDEO_PREP_IMAGE := $(REGION)-docker.pkg.dev/$(shell gcloud config get-value project)/showrunner/worker-video-prep:$(shell git rev-parse --short HEAD))
	gcloud builds submit backend --config backend/docker/cloudbuild.video-prep.yaml \
	  --substitutions _IMAGE=$(VIDEO_PREP_IMAGE)
	gcloud run deploy worker-video-prep --image $(VIDEO_PREP_IMAGE) --region $(REGION) \
	  --update-env-vars SERVICE=worker-video-prep

# The reel renderer needs ffmpeg + fonts + librosa, so it has its own Dockerfile and cannot use
# `--source` (which would pick up backend/Dockerfile, the wrong one) — same shape as deploy-face.
deploy-render:
	$(eval RENDER_IMAGE := $(REGION)-docker.pkg.dev/$(shell gcloud config get-value project)/showrunner/render:$(shell git rev-parse --short HEAD))
	gcloud builds submit backend --config backend/docker/cloudbuild.render.yaml \
	  --substitutions _IMAGE=$(RENDER_IMAGE)
	./deploy/render.sh $(RENDER_IMAGE)

deploy-publisher:
	gcloud run deploy publisher --source backend --region $(REGION) \
	  --update-env-vars SERVICE=publisher

# Static PWA export → Firebase Hosting. Rebuild first so out/ reflects frontend/.env.local.
deploy-hosting:
	cd frontend && npm run build
	firebase deploy --only hosting --project showrunner-hq
