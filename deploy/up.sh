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

step "Deploy api (guest-facing, public — auth is enforced in-app via Firebase ID tokens)"
gcloud run deploy api \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "$(sa_email "${SA_API}")" \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 10 --concurrency 80 \
  --timeout 60 --allow-unauthenticated \
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

step "Health"
for svc in api intake dlq worker-curate worker-face; do
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
Next: make dev-event  →  make smoke
SUMMARY
