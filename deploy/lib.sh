#!/usr/bin/env bash
# Shared plumbing for every deploy script. Sourced, never run directly.
#
# The names here (buckets, queues, service accounts) must match what the services read from the
# environment, so `.env` is the single source of truth and these scripts write back into it when
# they have to pick a different name (e.g. a globally-taken bucket).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

load_env() {
  # Mirrors backend/shared/settings.py's loader: existing environment wins, ` #` starts a comment.
  [[ -f "${ENV_FILE}" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    value="${value%% #*}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [[ -n "${key}" ]] || continue
    if [[ -z "${!key:-}" ]]; then export "${key}=${value}"; fi
  done < "${ENV_FILE}"
}

upsert_env() {
  # Record a resolved name back into .env so the next script (and the app) agree with reality.
  local key="$1" value="$2"
  touch "${ENV_FILE}"
  if grep -qE "^${key}=" "${ENV_FILE}"; then
    if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
      sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
      sed -i.bak "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}" && rm -f "${ENV_FILE}.bak"
    fi
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
  export "${key}=${value}"
}

load_env

PROJECT_ID="${GCP_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "your-gcp-project-id" ]]; then
  echo "No project configured. Set GOOGLE_CLOUD_PROJECT in .env or run: gcloud config set project" >&2
  exit 1
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

# Bucket names default to the .env values; buckets.sh falls back to a project-prefixed name if
# one is already taken globally (pre-authorized in HANDOFF §9) and rewrites .env accordingly.
RAW_BUCKET="${RAW_MEDIA_BUCKET:-showrunner-raw-media}"
DERIVED_BUCKET="${DERIVED_MEDIA_BUCKET:-showrunner-derived-media}"
CURATED_BUCKET="${CURATED_REELS_BUCKET:-showrunner-curated-reels}"

# Service accounts: one per service (spec 09 §4). `sa-tasks` is not a service — it is the OIDC
# identity Cloud Tasks presents when it calls a worker.
SA_API="sa-api"
SA_INTAKE="sa-intake"
SA_DLQ="sa-dlq"
SA_CURATE="sa-curate"
SA_FACE="sa-face"
SA_SAFETY="sa-safety"
SA_PUBLISHER="sa-publisher"
# The reel renderer, and the only identity in the fleet permitted to *write* the curated bucket
# (spec 09 §4). It reads `derived` and has no grant on `raw` at all — a reel is built from the same
# 1600 px render the gallery serves, never from a guest's original.
SA_RENDER="sa-render"
SA_TASKS="sa-tasks"
SA_EVENTARC="sa-eventarc"
# The identity Cloud Scheduler presents to `api`'s /internal/tick over OIDC. It holds no project
# roles at all — `api` is public, so nothing needs to be granted for the call to land, and the
# allowlist inside the handler (shared/oidc.py) is what makes the identity meaningful.
SA_SCHEDULER="sa-scheduler"

DLQ_TOPIC="eventarc-dlq"

sa_email() { printf '%s@%s.iam.gserviceaccount.com' "$1" "${PROJECT_ID}"; }

step() { printf '\n\033[1m── %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

ensure_sa() {
  local name="$1" display="$2"
  if gcloud iam service-accounts describe "$(sa_email "${name}")" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    note "service account ${name} exists"
  else
    gcloud iam service-accounts create "${name}" \
      --display-name "${display}" --project "${PROJECT_ID}" >/dev/null
    note "service account ${name} created"
  fi
}

grant_project_role() {
  # add-iam-policy-binding is idempotent; --condition=None keeps it quiet in non-interactive runs.
  local member="$1" role="$2"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "${member}" --role "${role}" --condition=None >/dev/null
  note "project: ${role} → ${member}"
}

grant_sa_role() {
  local target_sa="$1" member="$2" role="$3"
  gcloud iam service-accounts add-iam-policy-binding "${target_sa}" \
    --member "${member}" --role "${role}" --project "${PROJECT_ID}" >/dev/null
  note "sa ${target_sa}: ${role} → ${member}"
}

grant_bucket_role() {
  local bucket="$1" member="$2" role="$3"
  gcloud storage buckets add-iam-policy-binding "gs://${bucket}" \
    --member "${member}" --role "${role}" >/dev/null
  note "gs://${bucket}: ${role} → ${member}"
}

grant_run_invoker() {
  # Per-service, not project-wide: sa-tasks may call exactly the workers it dispatches to.
  local service="$1" member="$2"
  gcloud run services add-iam-policy-binding "${service}" \
    --member "${member}" --role "roles/run.invoker" \
    --region "${REGION}" --project "${PROJECT_ID}" >/dev/null
  note "run ${service}: roles/run.invoker → ${member}"
}

grant_run_job_invoker() {
  # Per-job, not project-wide: `api` may start the render job and nothing else. `run.developer` is the
  # role that carries `run.jobs.run`; there is no narrower predefined one for starting an execution.
  local job="$1" member="$2"
  gcloud run jobs add-iam-policy-binding "${job}" \
    --member "${member}" --role "roles/run.developer" \
    --region "${REGION}" --project "${PROJECT_ID}" >/dev/null
  note "run job ${job}: roles/run.developer → ${member}"
}

run_url() {
  gcloud run services describe "$1" --region "${REGION}" --project "${PROJECT_ID}" \
    --format='value(status.url)' 2>/dev/null || true
}
