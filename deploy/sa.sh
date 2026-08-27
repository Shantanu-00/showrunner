#!/usr/bin/env bash
# One least-privilege service account per service (spec 09 §4). Idempotent.
#
# Bucket-scoped grants live in buckets.sh — they need the buckets to exist first. Everything
# here is project-level or SA-level and can run against an empty project.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

API_SA="$(sa_email "${SA_API}")"
INTAKE_SA="$(sa_email "${SA_INTAKE}")"
DLQ_SA="$(sa_email "${SA_DLQ}")"
CURATE_SA="$(sa_email "${SA_CURATE}")"
FACE_SA="$(sa_email "${SA_FACE}")"
TASKS_SA="$(sa_email "${SA_TASKS}")"
EVENTARC_SA="$(sa_email "${SA_EVENTARC}")"

step "Creating service accounts"
ensure_sa "${SA_API}" "Showrunner api (guest-facing surface)"
ensure_sa "${SA_INTAKE}" "Showrunner intake (Eventarc target)"
ensure_sa "${SA_DLQ}" "Showrunner dlq (dead-letter consumer)"
ensure_sa "${SA_CURATE}" "Showrunner worker-curate (Curator agent)"
ensure_sa "${SA_FACE}" "Showrunner worker-face (Face Indexer)"
ensure_sa "${SA_TASKS}" "Showrunner Cloud Tasks OIDC identity"
ensure_sa "${SA_EVENTARC}" "Showrunner Eventarc / Pub-Sub delivery identity"

step "api: Firestore, Tasks, custom claims"
grant_project_role "serviceAccount:${API_SA}" "roles/datastore.user"
grant_project_role "serviceAccount:${API_SA}" "roles/cloudtasks.enqueuer"
# Custom claims (personId / host / platformAdmin) are the only trusted identity in the system
# (spec 02 §1), and the api service is what mints them.
grant_project_role "serviceAccount:${API_SA}" "roles/firebaseauth.admin"

step "api: signBlob on itself (signed URLs without a key file)"
# Signed URLs need a signer. Instead of a private key on disk we call IAM signBlob as ourselves,
# which requires tokenCreator on our own identity. This is the whole reason no key file exists.
grant_sa_role "${API_SA}" "serviceAccount:${API_SA}" "roles/iam.serviceAccountTokenCreator"

DEV_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
if [[ -n "${DEV_ACCOUNT}" && "${DEV_ACCOUNT}" != "(unset)" ]]; then
  # Local runs use ADC (a user account, which cannot sign) — impersonating sa-api gives the same
  # signing path on a laptop as on Cloud Run.
  grant_sa_role "${API_SA}" "user:${DEV_ACCOUNT}" "roles/iam.serviceAccountTokenCreator"
fi

step "intake: Firestore, Tasks, Eventarc"
grant_project_role "serviceAccount:${INTAKE_SA}" "roles/datastore.user"
grant_project_role "serviceAccount:${INTAKE_SA}" "roles/cloudtasks.enqueuer"
grant_project_role "serviceAccount:${INTAKE_SA}" "roles/eventarc.eventReceiver"

step "dlq: Firestore only"
grant_project_role "serviceAccount:${DLQ_SA}" "roles/datastore.user"

step "worker-curate: Firestore + Vertex AI"
grant_project_role "serviceAccount:${CURATE_SA}" "roles/datastore.user"
# The Curator's one paid call. `aiplatform.user` is the narrowest role that permits
# generateContent on a publisher model; the worker has no other Vertex surface.
grant_project_role "serviceAccount:${CURATE_SA}" "roles/aiplatform.user"

step "worker-face: Firestore only — no LLM, no Vertex, no raw bucket"
grant_project_role "serviceAccount:${FACE_SA}" "roles/datastore.user"

step "Cloud Tasks OIDC: actAs for the enqueuers"
# Creating a task that carries an OIDC token means acting as that identity — without this the
# first B2 dispatch fails with an opaque 403.
grant_sa_role "${TASKS_SA}" "serviceAccount:${API_SA}" "roles/iam.serviceAccountUser"
grant_sa_role "${TASKS_SA}" "serviceAccount:${INTAKE_SA}" "roles/iam.serviceAccountUser"

step "Eventarc / Pub-Sub delivery identity"
grant_project_role "serviceAccount:${EVENTARC_SA}" "roles/eventarc.eventReceiver"
# Pub/Sub mints the OIDC token for the dead-letter push subscription, so its service agent needs
# to be able to impersonate our delivery identity.
grant_sa_role "${EVENTARC_SA}" \
  "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  "roles/iam.serviceAccountTokenCreator"

step "GCS service agent: publish object notifications"
GCS_AGENT="$(gcloud storage service-agent --project "${PROJECT_ID}" 2>/dev/null || echo "service-${PROJECT_NUMBER}@gs-project-accounts.iam.gserviceaccount.com")"
grant_project_role "serviceAccount:${GCS_AGENT}" "roles/pubsub.publisher"

step "Recording identities in .env"
upsert_env SIGNER_SA_EMAIL "${API_SA}"
upsert_env TASKS_SA_EMAIL "${TASKS_SA}"
note "SIGNER_SA_EMAIL=${API_SA}"
note "TASKS_SA_EMAIL=${TASKS_SA}"

echo
echo "Service accounts ready. IAM propagation can lag ~60 s on first use."
