.PHONY: up down dev-event smoke smoke-faces smoke-safety seed eval rules-test \
        deploy-api deploy-intake deploy-dlq deploy-curate deploy-face deploy-safety \
        deploy-video-prep deploy-render deploy-publisher

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

seed:
	$(PY) backend/seed.py --event demo

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

deploy-video-prep:
	gcloud run deploy worker-video-prep --source backend --region $(REGION) \
	  --update-env-vars SERVICE=worker-video-prep

deploy-render:
	gcloud run jobs deploy render --source backend --region $(REGION)

deploy-publisher:
	gcloud run deploy publisher --source backend --region $(REGION) \
	  --update-env-vars SERVICE=publisher
