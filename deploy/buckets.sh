#!/usr/bin/env bash
# Three buckets, all private, all uniform-access (spec 01 §4). Idempotent.
#
# raw → guest originals (the only bucket with a finalize trigger)
# derived → thumbs/renders/quarantine (separate bucket so intake never retriggers itself)
# curated → published reels
#
# Bucket names are globally unique, so if the .env name is taken this falls back to a
# project-prefixed name and rewrites .env (pre-authorized in HANDOFF §9).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

API_SA="$(sa_email "${SA_API}")"
INTAKE_SA="$(sa_email "${SA_INTAKE}")"
CURATE_SA="$(sa_email "${SA_CURATE}")"
FACE_SA="$(sa_email "${SA_FACE}")"
SAFETY_SA="$(sa_email "${SA_SAFETY}")"

CORS_DIR="$(mktemp -d)"
trap 'rm -rf "${CORS_DIR}"' EXIT

# Browser origins that talk to GCS directly. The PWA PUTs bytes to raw and reads renders from
# derived through signed URLs; both are cross-origin, so CORS is not optional.
ORIGINS="\"http://localhost:3000\", \"https://${PROJECT_ID}.web.app\", \"https://${PROJECT_ID}.firebaseapp.com\""
if [[ -n "${NEXT_PUBLIC_APP_ORIGIN:-}" ]]; then
  ORIGINS="${ORIGINS}, \"${NEXT_PUBLIC_APP_ORIGIN}\""
fi

cat > "${CORS_DIR}/raw.json" <<JSON
[
  {
    "origin": [${ORIGINS}],
    "method": ["PUT", "POST", "GET", "HEAD"],
    "responseHeader": ["Content-Type", "Content-Length", "ETag", "Location", "Range", "x-goog-resumable"],
    "maxAgeSeconds": 3600
  }
]
JSON

cat > "${CORS_DIR}/read.json" <<JSON
[
  {
    "origin": [${ORIGINS}],
    "method": ["GET", "HEAD"],
    "responseHeader": ["Content-Type", "ETag", "Range"],
    "maxAgeSeconds": 3600
  }
]
JSON

RESOLVED_BUCKET=""

ensure_bucket() {
  local key="$1" name="$2" suffix="$3"
  local create_args=(
    --location "${REGION}"
    --uniform-bucket-level-access
    --public-access-prevention
    --project "${PROJECT_ID}"
  )
  if gcloud storage buckets describe "gs://${name}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    note "gs://${name} exists"
  elif gcloud storage buckets create "gs://${name}" "${create_args[@]}" >/dev/null 2>&1; then
    note "gs://${name} created"
  else
    local alt="${PROJECT_ID}-${suffix}"
    note "gs://${name} unavailable (name taken) — using gs://${alt}"
    if ! gcloud storage buckets describe "gs://${alt}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      gcloud storage buckets create "gs://${alt}" "${create_args[@]}" >/dev/null
    fi
    name="${alt}"
  fi
  upsert_env "${key}" "${name}"
  RESOLVED_BUCKET="${name}"
}

step "Buckets"
ensure_bucket RAW_MEDIA_BUCKET "${RAW_BUCKET}" "raw-media"
RAW_BUCKET="${RESOLVED_BUCKET}"
ensure_bucket DERIVED_MEDIA_BUCKET "${DERIVED_BUCKET}" "derived-media"
DERIVED_BUCKET="${RESOLVED_BUCKET}"
ensure_bucket CURATED_REELS_BUCKET "${CURATED_BUCKET}" "curated-reels"
CURATED_BUCKET="${RESOLVED_BUCKET}"

step "CORS"
gcloud storage buckets update "gs://${RAW_BUCKET}" --cors-file "${CORS_DIR}/raw.json" >/dev/null
note "gs://${RAW_BUCKET}: PUT/POST/GET/HEAD"
gcloud storage buckets update "gs://${DERIVED_BUCKET}" --cors-file "${CORS_DIR}/read.json" >/dev/null
note "gs://${DERIVED_BUCKET}: GET/HEAD"
gcloud storage buckets update "gs://${CURATED_BUCKET}" --cors-file "${CORS_DIR}/read.json" >/dev/null
note "gs://${CURATED_BUCKET}: GET/HEAD"

step "Bucket IAM (scoped per bucket, never project-wide storage roles)"
# api: hands out signed URLs, and deletes objects when a guest retracts consent (spec 02).
grant_bucket_role "${RAW_BUCKET}" "serviceAccount:${API_SA}" "roles/storage.objectAdmin"
grant_bucket_role "${DERIVED_BUCKET}" "serviceAccount:${API_SA}" "roles/storage.objectAdmin"
grant_bucket_role "${CURATED_BUCKET}" "serviceAccount:${API_SA}" "roles/storage.objectViewer"
# intake: reads the original, rewrites it once to strip GPS, deletes rejected/orphaned bytes.
grant_bucket_role "${RAW_BUCKET}" "serviceAccount:${INTAKE_SA}" "roles/storage.objectAdmin"
# ...and only ever creates in derived.
grant_bucket_role "${DERIVED_BUCKET}" "serviceAccount:${INTAKE_SA}" "roles/storage.objectCreator"
# worker-curate: reads one render (classify_768 / poster) and writes nothing to any bucket. It has
# no grant at all on raw — the Curator never sees a guest's full-resolution original.
grant_bucket_role "${DERIVED_BUCKET}" "serviceAccount:${CURATE_SA}" "roles/storage.objectViewer"
# worker-face: reads display_1600 (falling back to classify_768) for the faces stage; the selfie
# path never touches GCS at all (the base64 body goes straight to /embed). Same no-raw-bucket
# posture as worker-curate — it never sees a guest's original either.
grant_bucket_role "${DERIVED_BUCKET}" "serviceAccount:${FACE_SA}" "roles/storage.objectViewer"
# worker-safety: reads classify_768 for both Guardian passes (Vision charges per image, not per
# pixel, so the small render is the right one). No raw grant either — the item most likely to be
# genuinely sensitive is the one this worker looks at, and it still never sees the original.
grant_bucket_role "${DERIVED_BUCKET}" "serviceAccount:${SAFETY_SA}" "roles/storage.objectViewer"

echo
echo "Buckets ready: ${RAW_BUCKET} · ${DERIVED_BUCKET} · ${CURATED_BUCKET}"
