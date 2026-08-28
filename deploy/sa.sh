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
SAFETY_SA="$(sa_email "${SA_SAFETY}")"
PUBLISHER_SA="$(sa_email "${SA_PUBLISHER}")"
RENDER_SA="$(sa_email "${SA_RENDER}")"
TASKS_SA="$(sa_email "${SA_TASKS}")"
SCHEDULER_SA="$(sa_email "${SA_SCHEDULER}")"
EVENTARC_SA="$(sa_email "${SA_EVENTARC}")"

step "Creating service accounts"
ensure_sa "${SA_API}" "Showrunner api (guest-facing surface)"
ensure_sa "${SA_INTAKE}" "Showrunner intake (Eventarc target)"
ensure_sa "${SA_DLQ}" "Showrunner dlq (dead-letter consumer)"
ensure_sa "${SA_CURATE}" "Showrunner worker-curate (Curator agent)"
ensure_sa "${SA_FACE}" "Showrunner worker-face (Face Indexer)"
ensure_sa "${SA_SAFETY}" "Showrunner worker-safety (Guardian)"
ensure_sa "${SA_PUBLISHER}" "Showrunner publisher (kiosk playlist)"
ensure_sa "${SA_RENDER}" "Showrunner render job (Reel Director)"
ensure_sa "${SA_TASKS}" "Showrunner Cloud Tasks OIDC identity"
ensure_sa "${SA_SCHEDULER}" "Showrunner Cloud Scheduler OIDC identity"
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

step "worker-safety: Firestore + Vertex AI + Vision (the Guardian's two passes)"
grant_project_role "serviceAccount:${SAFETY_SA}" "roles/datastore.user"
# Pass 2, the dignity rubric. Same narrowest-role reasoning as sa-curate: `aiplatform.user` permits
# generateContent on a publisher model and nothing else.
grant_project_role "serviceAccount:${SAFETY_SA}" "roles/aiplatform.user"
# Pass 1, SafeSearch. Vision's annotate methods carry no IAM permission of their own — access is
# granted by being able to consume the project's Vision quota, which is what this role is.
grant_project_role "serviceAccount:${SAFETY_SA}" "roles/serviceusage.serviceUsageConsumer"

step "publisher: Firestore only — no LLM, no Vertex, no GCS, no Tasks"
# The playlist writer reads media/people/bounties/reels and writes one document. It has no reason to
# reach a model or a bucket, and spec 04 §1's "judgment by agents, enforcement by policy" is easier to
# believe about the most visible surface in the product when the service that owns it *cannot* call a
# model even if a future session wanted it to.
grant_project_role "serviceAccount:${PUBLISHER_SA}" "roles/datastore.user"

step "render: Firestore + Vertex AI (the Reel Director's own model calls and Lyria)"
# The render job runs spec 06 §3 end to end in one execution — DIRECT (gemini-3.7-flash), CRITIC
# (flash-lite), Lyria 3, ffmpeg, publish — so it needs `aiplatform.user`, the same narrowest role
# sa-curate and sa-safety carry. Bucket grants live in buckets.sh: read on `derived`, create on
# `curated`, and deliberately **nothing** on `raw`.
grant_project_role "serviceAccount:${RENDER_SA}" "roles/datastore.user"
grant_project_role "serviceAccount:${RENDER_SA}" "roles/aiplatform.user"
# `directors/reel/agent.py` guards its DIRECT prompt through the same Model Armor plugin as the
# Story Director (§4.20) — missed the first time this grant list was written, since sa-api's own
# modelarmor.user grant below reads as "Model Armor is covered" until you check which SA is
# actually calling `guard()` on the reel's own storyboard prompt. Caught live: a real commission
# 403'd on `modelarmor.templates.useToSanitizeUserPrompt`, not fatal (armor.py fails open) but not
# the protection the docstring claims either.
grant_project_role "serviceAccount:${RENDER_SA}" "roles/modelarmor.user"
# Signed GET URLs for the finished file are minted by `api` as sa-api, not here — the renderer writes
# the object and never hands anyone a link to it.

step "api: act as the render job's identity (spec 06 §1's commissions)"
# The Story Director's COMMISSION_REEL action and the host's console button both start a Cloud Run Job
# execution, and starting a job means acting as that job's runtime identity. The `run.developer` grant
# on the job itself lives in deploy/render.sh, because it needs the job to exist first.
grant_sa_role "${RENDER_SA}" "serviceAccount:$(sa_email "${SA_API}")" "roles/iam.serviceAccountUser"

step "scheduler: OIDC token minting for the director tick"
# Cloud Scheduler impersonates this identity to mint the OIDC token it presents to `api`. Its own
# service agent needs tokenCreator to do that — usually auto-granted with the API, granted explicitly
# here so a clean project works on the first run rather than the second (same shape as Pub/Sub below).
grant_sa_role "${SCHEDULER_SA}" \
  "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  "roles/iam.serviceAccountTokenCreator"

step "api: Vertex AI — the Story Director's one model call (spec 05 §1)"
# `api` hosts the director: the tick handler already holds the per-event lease, and spec 05 §1's
# guardrail set is deterministic code that has to run inside it (HANDOFF §4.20). So the service that
# serves guests now also calls `gemini-3.7-flash` once per event per tick, plus `flash-lite` per bounty
# submission. `aiplatform.user` is the same narrowest role sa-curate and sa-safety carry — it permits
# generateContent on a publisher model and nothing else. Note what is deliberately *not* granted:
# no bucket access. The director reasons over the Curator's stored description of a photograph, never
# over the photograph, so the service running the fleet's planner cannot read a guest's media.
grant_project_role "serviceAccount:${API_SA}" "roles/aiplatform.user"

step "api: Model Armor on text surfaces (spec 09 §4.5)"
# The itinerary paste, bounty briefs and captions are sanitized before they reach a prompt
# (services/armor.py). `modelarmor.user` is the sanitize-only role — it cannot create or edit the
# template, which is created once by deploy/bootstrap.sh.
grant_project_role "serviceAccount:${API_SA}" "roles/modelarmor.user"

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
# `api` allowlists these two emails on /internal/tick (shared/oidc.py). It is the access control on
# the one public endpoint that makes the fleet act, so the deploy writes it rather than a human.
upsert_env SCHEDULER_SA_EMAIL "${SCHEDULER_SA}"
note "SIGNER_SA_EMAIL=${API_SA}"
note "TASKS_SA_EMAIL=${TASKS_SA}"
note "SCHEDULER_SA_EMAIL=${SCHEDULER_SA}"

echo
echo "Service accounts ready. IAM propagation can lag ~60 s on first use."
