#!/usr/bin/env bash
# Stand up everything that exists, in dependency order. Idempotent — this is the only command
# needed to go from an empty project to a working upload path.
#
#   bootstrap (APIs) → service accounts → buckets → Firestore → queues → build → deploy → Eventarc
#
# Services and their scaling come from spec 09 §1. One image serves api/intake/dlq/worker-curate;
# $SERVICE selects which (see backend/main.py), so this builds once and deploys four times.
#
# Deploy order matters in one place: `worker-curate` goes up *before* `intake`, because intake
# dispatches to it by URL and a Cloud Tasks target it does not know about is a silently skipped
# dispatch, not an error. Anything that produces work is deployed after the thing that consumes it.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SKIP_BOOTSTRAP="${SKIP_BOOTSTRAP:-0}"
REPO="showrunner"
IMAGE_HOST="${REGION}-docker.pkg.dev"
TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo manual)"
IMAGE="${IMAGE_HOST}/${PROJECT_ID}/${REPO}/backend:${TAG}"

echo "Project ${PROJECT_ID} · region ${REGION} · image tag ${TAG}"

if [[ "${SKIP_BOOTSTRAP}" != "1" ]]; then
  "${REPO_ROOT}/deploy/bootstrap.sh"
fi
"${REPO_ROOT}/deploy/sa.sh"
"${REPO_ROOT}/deploy/buckets.sh"
"${REPO_ROOT}/deploy/firestore.sh"
"${REPO_ROOT}/deploy/queues.sh"

# buckets.sh / sa.sh may have rewritten .env (fallback bucket names, SA emails) — re-read it so the
# deploys below pass the names the services will actually use.
unset RAW_MEDIA_BUCKET DERIVED_MEDIA_BUCKET CURATED_REELS_BUCKET SIGNER_SA_EMAIL TASKS_SA_EMAIL
load_env
RAW_BUCKET="${RAW_MEDIA_BUCKET}"
DERIVED_BUCKET="${DERIVED_MEDIA_BUCKET}"
CURATED_BUCKET="${CURATED_REELS_BUCKET}"

step "Artifact Registry"
if gcloud artifacts repositories describe "${REPO}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  note "repository ${REPO} exists"
else
  gcloud artifacts repositories create "${REPO}" \
    --repository-format docker --location "${REGION}" \
    --description "Showrunner service images" --project "${PROJECT_ID}" >/dev/null
  note "repository ${REPO} created"
fi

step "Build (Cloud Build — no local Docker on this machine)"
gcloud builds submit "${REPO_ROOT}/backend" --tag "${IMAGE}" --project "${PROJECT_ID}" >/dev/null
note "built ${IMAGE}"

# worker-face ships InsightFace baked in (~700 MB of native deps) and cannot ride the common
# image without bloating api/intake's cold start — its own Dockerfile, its own build (spec 09 §1).
FACE_IMAGE="${IMAGE_HOST}/${PROJECT_ID}/${REPO}/worker-face:${TAG}"
step "Build worker-face (separate image — backend/docker/Dockerfile.face)"
gcloud builds submit "${REPO_ROOT}/backend" \
  --config "${REPO_ROOT}/backend/docker/cloudbuild.face.yaml" \
  --substitutions "_IMAGE=${FACE_IMAGE}" --project "${PROJECT_ID}" >/dev/null
note "built ${FACE_IMAGE}"

# Same story for the reel renderer: ffmpeg + fonts + librosa's SciPy/numba stack is ~400 MB that
# api/intake must never carry (spec 09 §1 gives `render` its own row and its own 8 vCPU / 32 GiB shape).
RENDER_IMAGE="${IMAGE_HOST}/${PROJECT_ID}/${REPO}/render:${TAG}"
step "Build render (separate image — backend/docker/Dockerfile.render)"
gcloud builds submit "${REPO_ROOT}/backend" \
  --config "${REPO_ROOT}/backend/docker/cloudbuild.render.yaml" \
  --substitutions "_IMAGE=${RENDER_IMAGE}" --project "${PROJECT_ID}" >/dev/null
note "built ${RENDER_IMAGE}"

# Shared runtime config. Worker URLs stay empty until B2 exists; tasks.enqueue logs a skipped
# dispatch rather than queueing work nothing can consume.
COMMON_ENV="ENVIRONMENT=${ENVIRONMENT:-production}"
COMMON_ENV="${COMMON_ENV};GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
COMMON_ENV="${COMMON_ENV};GOOGLE_CLOUD_LOCATION=${REGION}"
# The GenAI publisher models serve from `global`, not from ${REGION} — a regional call 404s
# (friction log 2026-08-27). This is the model endpoint only; Run/Tasks/Firestore stay regional.
COMMON_ENV="${COMMON_ENV};GENAI_LOCATION=${GENAI_LOCATION:-global}"
COMMON_ENV="${COMMON_ENV};MODEL_CLASSIFIER=${MODEL_CLASSIFIER:-gemini-3.5-flash-lite}"
COMMON_ENV="${COMMON_ENV};MODEL_DIRECTOR=${MODEL_DIRECTOR:-gemini-3.7-flash}"
# Lyria 3 Clip — every reel's soundtrack (spec 06 §3, bonus +0.2). Only the render job calls it, but the
# value rides in COMMON_ENV so a `make deploy-api` cannot leave the two halves disagreeing about it.
COMMON_ENV="${COMMON_ENV};MODEL_MUSIC=${MODEL_MUSIC:-lyria-3-clip-preview}"
COMMON_ENV="${COMMON_ENV};RENDER_JOB_NAME=${RENDER_JOB_NAME:-render}"
COMMON_ENV="${COMMON_ENV};RAW_MEDIA_BUCKET=${RAW_BUCKET}"
COMMON_ENV="${COMMON_ENV};DERIVED_MEDIA_BUCKET=${DERIVED_BUCKET}"
COMMON_ENV="${COMMON_ENV};CURATED_REELS_BUCKET=${CURATED_BUCKET}"
COMMON_ENV="${COMMON_ENV};CLASSIFY_QUEUE=${CLASSIFY_QUEUE:-classify-queue}"
COMMON_ENV="${COMMON_ENV};FACE_QUEUE=${FACE_QUEUE:-face-queue}"
COMMON_ENV="${COMMON_ENV};SAFETY_QUEUE=${SAFETY_QUEUE:-safety-queue}"
COMMON_ENV="${COMMON_ENV};VIDEO_PREP_QUEUE=${VIDEO_PREP_QUEUE:-video-prep-queue}"
COMMON_ENV="${COMMON_ENV};PRIORITY_QUEUE=${PRIORITY_QUEUE:-priority-queue}"
COMMON_ENV="${COMMON_ENV};RENDERS_QUEUE=${RENDERS_QUEUE:-renders-queue}"
COMMON_ENV="${COMMON_ENV};TASKS_SA_EMAIL=${TASKS_SA_EMAIL}"
COMMON_ENV="${COMMON_ENV};SIGNER_SA_EMAIL=${SIGNER_SA_EMAIL}"
# Model Armor guards text surfaces only (spec 03 §4.7). The template is created idempotently by
# bootstrap.sh in the `us` multi-region — services/armor.py reads the location out of this resource
# name rather than taking it as a second env var, so the two can never disagree.
COMMON_ENV="${COMMON_ENV};MODEL_ARMOR_TEMPLATE=${MODEL_ARMOR_TEMPLATE:-projects/${PROJECT_ID}/locations/us/templates/showrunner-guard}"

APP_ORIGINS="http://localhost:3000,https://${PROJECT_ID}.web.app,https://${PROJECT_ID}.firebaseapp.com"

# ALLOWED_ORIGINS is itself comma-separated, which collides with --set-env-vars' default comma
# delimiter between KEY=VALUE pairs — so every deploy below uses the `^;^` alternate-delimiter
# syntax (semicolons split pairs; commas inside a value are then just characters).
step "Deploy worker-curate (Cloud Tasks target, private — spec 09 §1: 1/1Gi, 0→10, concurrency 8)"
gcloud run deploy worker-curate \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_CURATE}")" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 10 --concurrency 8 \
  --timeout 120 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=worker-curate;${COMMON_ENV}" \
  --quiet >/dev/null
CURATE_URL="$(run_url worker-curate)"
note "worker-curate → ${CURATE_URL}"

# Only sa-tasks may call it, and only this service. The handler additionally trusts nothing from
# the request body it has not re-read from Firestore.
grant_run_invoker worker-curate "serviceAccount:${TASKS_SA_EMAIL}"

# Now that the target exists, intake can be told where to dispatch. Recorded in .env too, so a
# `make deploy-intake` later keeps the wiring instead of quietly reverting to skipped dispatches.
upsert_env WORKER_CURATE_URL "${CURATE_URL}"
COMMON_ENV="${COMMON_ENV};WORKER_CURATE_URL=${CURATE_URL}"

step "Deploy worker-face (Cloud Tasks target + api's /embed call, private — spec 09 §1: 2/4Gi, 0→5 min 1, concurrency 4)"
gcloud run deploy worker-face \
  --image "${FACE_IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_FACE}")" \
  --cpu 2 --memory 4Gi --min-instances 1 --max-instances 5 --concurrency 4 \
  --timeout 120 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=worker-face;${COMMON_ENV}" \
  --quiet >/dev/null
FACE_URL="$(run_url worker-face)"
note "worker-face → ${FACE_URL}"

# Two callers, two reasons: sa-tasks dispatches the `faces` stage task, sa-api calls /embed
# synchronously from the selfie enrollment/re-claim endpoints (spec 02 §3).
grant_run_invoker worker-face "serviceAccount:${TASKS_SA_EMAIL}"
grant_run_invoker worker-face "serviceAccount:$(sa_email "${SA_API}")"

upsert_env WORKER_FACE_URL "${FACE_URL}"
COMMON_ENV="${COMMON_ENV};WORKER_FACE_URL=${FACE_URL}"

step "Deploy worker-safety (Cloud Tasks target, private — spec 09 §1: 1/1Gi, 0→10, concurrency 8)"
gcloud run deploy worker-safety \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_SAFETY}")" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 10 --concurrency 8 \
  --timeout 120 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=worker-safety;${COMMON_ENV}" \
  --quiet >/dev/null
SAFETY_URL="$(run_url worker-safety)"
note "worker-safety → ${SAFETY_URL}"

grant_run_invoker worker-safety "serviceAccount:${TASKS_SA_EMAIL}"

upsert_env WORKER_SAFETY_URL "${SAFETY_URL}"
COMMON_ENV="${COMMON_ENV};WORKER_SAFETY_URL=${SAFETY_URL}"

# The publisher goes up before `api`, because the director tick nudges it by URL (spec 04 §4's
# fallback recompute trigger) and an unset PUBLISHER_URL would make that a silently skipped call.
step "Deploy publisher (kiosk playlist writer, private — spec 09 §1: 1/512Mi, min 1/max 5)"
# `--no-cpu-throttling` is not a performance dial here, it is a correctness requirement. This service
# holds Firestore *listeners* on a background thread; with CPU allocated only during requests, that
# thread is frozen between requests and the wall silently stops updating — no error, no log, just a
# stale kiosk. `min-instances=1` (spec 09 §1) is the other half of the same requirement: scale-to-zero
# kills the listener outright. Both are why the tick can also nudge it over HTTP.
gcloud run deploy publisher \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_PUBLISHER}")" \
  --cpu 1 --memory 512Mi --min-instances 1 --max-instances 5 --concurrency 20 \
  --no-cpu-throttling --timeout 120 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=publisher;${COMMON_ENV}" \
  --quiet >/dev/null
PUBLISHER_URL="$(run_url publisher)"
note "publisher → ${PUBLISHER_URL}"

# Only `api` may nudge it, and only on /recompute. Nothing else in the fleet calls the publisher:
# every other trigger arrives as a Firestore change on a document some worker already wrote.
grant_run_invoker publisher "serviceAccount:$(sa_email "${SA_API}")"

upsert_env PUBLISHER_URL "${PUBLISHER_URL}"
COMMON_ENV="${COMMON_ENV};PUBLISHER_URL=${PUBLISHER_URL}"
COMMON_ENV="${COMMON_ENV};SCHEDULER_SA_EMAIL=${SCHEDULER_SA_EMAIL}"

step "Deploy api (guest-facing, public — auth is enforced in-app via Firebase ID tokens)"
# `--timeout 300`, up from 60: since S8b this service also runs the Story Director, and one invocation
# of `/internal/tick` fans out over every live event, each costing a `gemini-3.7-flash` call plus a
# publisher nudge. Guest requests are unaffected (they finish in milliseconds); what the old 60 s
# ceiling would have truncated is the tick, silently, once a few events were live at once. Spec 09 §1
# pins this service's CPU, memory, scaling and concurrency — not its request timeout.
gcloud run deploy api \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_API}")" \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 10 --concurrency 80 \
  --timeout 300 --allow-unauthenticated \
  --set-env-vars "^;^SERVICE=api;${COMMON_ENV};ALLOWED_ORIGINS=${APP_ORIGINS}" \
  --quiet >/dev/null
note "api → $(run_url api)"

step "Deploy intake (Eventarc target, private)"
gcloud run deploy intake \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_INTAKE}")" \
  --cpu 2 --memory 2Gi --min-instances 0 --max-instances 20 --concurrency 10 \
  --timeout 300 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=intake;${COMMON_ENV}" \
  --quiet >/dev/null
note "intake → $(run_url intake)"

step "Deploy dlq (dead-letter consumer, private)"
gcloud run deploy dlq \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_DLQ}")" \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 3 --concurrency 10 \
  --timeout 60 --no-allow-unauthenticated \
  --set-env-vars "^;^SERVICE=dlq;${COMMON_ENV}" \
  --quiet >/dev/null
note "dlq → $(run_url dlq)"

"${REPO_ROOT}/deploy/eventarc.sh"

API_URL="$(run_url api)"
upsert_env NEXT_PUBLIC_API_URL "${API_URL}"

# The render job goes up after `api`, because a published reel's `videoUri` is an `api` path (the kiosk's
# <video> cannot carry an auth header, so the document holds a URL that re-checks visibility and 302s to
# a signed URL — backend/api/reels.py). The renderer writes that URL, so it has to know it.
export RENDER_ENV="${COMMON_ENV};NEXT_PUBLIC_API_URL=${API_URL}"
"${REPO_ROOT}/deploy/render.sh" "${RENDER_IMAGE}"

# Last, because both jobs POST to the api URL that only exists once the deploy above finished.
"${REPO_ROOT}/deploy/scheduler.sh" "${API_URL}"

step "Health"
for svc in api intake dlq worker-curate worker-face worker-safety publisher; do
  url="$(run_url "${svc}")"
  if [[ "${svc}" == "api" ]]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' "${url}/livez")"
  else
    code="$(curl -s -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${url}/livez")"
  fi
  note "${svc}: HTTP ${code} ${url}"
done

cat <<SUMMARY

Up. Buckets: ${RAW_BUCKET} · ${DERIVED_BUCKET} · ${CURATED_BUCKET}
API: ${API_URL}
Publisher: ${PUBLISHER_URL}   (kiosk playlist writer, min-instances 1)
Render:    Cloud Run Job 'render' (8 vCPU / 32Gi, one execution per reel commission)
Scheduler: director-tick (2 min) · director-tick-demo (1 min + 30 s interleave)
Next: make dev-event  →  make smoke  →  make smoke-autonomy  →  make smoke-reel
SUMMARY
