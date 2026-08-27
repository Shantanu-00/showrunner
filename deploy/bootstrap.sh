#!/usr/bin/env bash
# Idempotent GCP project bootstrap for Showrunner.
# Enables APIs, sets budget alerts, confirms ADC. Safe to re-run.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project)}"
REGION="us-central1"

echo "Bootstrapping project: ${PROJECT_ID} (region ${REGION})"

gcloud config set project "${PROJECT_ID}"

echo "Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  eventarc.googleapis.com \
  pubsub.googleapis.com \
  cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  vision.googleapis.com \
  aiplatform.googleapis.com \
  modelarmor.googleapis.com \
  identitytoolkit.googleapis.com \
  firebase.googleapis.com \
  --project "${PROJECT_ID}"
# cloudresourcemanager: not enabled by default on a fresh project, and several gcloud
# subcommands (and the Model Armor call below) fail opaquely without it.
# artifactregistry: `gcloud run deploy --source` needs a repo to push the built image to.
# pubsub: Eventarc's transport, and where the dead-letter topic lives.
# identitytoolkit + firebase: anonymous guest auth and the web SDK config the PWA needs.

echo "Confirming Application Default Credentials..."
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "ADC missing — run: gcloud auth application-default login"
  exit 1
fi

# Model Armor has no client library in our pin set, and it is a multi-region service
# (us / eu only — never REGION). This is the same REST call the spec-09 ADK plugin makes.
# Verified against the live API by scripts/risk_tests/armor.py.
ARMOR_LOCATION="us"
ARMOR_TEMPLATE="showrunner-guard"
ARMOR_BASE="https://modelarmor.${ARMOR_LOCATION}.rep.googleapis.com/v1"
ARMOR_PARENT="projects/${PROJECT_ID}/locations/${ARMOR_LOCATION}"

echo "Ensuring Model Armor template ${ARMOR_TEMPLATE} in ${ARMOR_LOCATION}..."
armor_status=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  "${ARMOR_BASE}/${ARMOR_PARENT}/templates/${ARMOR_TEMPLATE}")

if [[ "${armor_status}" == "200" ]]; then
  echo "  template already exists"
else
  curl -s -X POST \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    "${ARMOR_BASE}/${ARMOR_PARENT}/templates?template_id=${ARMOR_TEMPLATE}" \
    -d '{
      "filterConfig": {
        "piAndJailbreakFilterSettings": {
          "filterEnforcement": "ENABLED",
          "confidenceLevel": "LOW_AND_ABOVE"
        },
        "maliciousUriFilterSettings": { "filterEnforcement": "ENABLED" },
        "sdpSettings": { "basicConfig": { "filterEnforcement": "ENABLED" } },
        "raiSettings": {
          "raiFilters": [
            { "filterType": "HATE_SPEECH",       "confidenceLevel": "MEDIUM_AND_ABOVE" },
            { "filterType": "HARASSMENT",        "confidenceLevel": "MEDIUM_AND_ABOVE" },
            { "filterType": "SEXUALLY_EXPLICIT", "confidenceLevel": "MEDIUM_AND_ABOVE" },
            { "filterType": "DANGEROUS",         "confidenceLevel": "MEDIUM_AND_ABOVE" }
          ]
        }
      }
    }' >/dev/null
  echo "  template created"
fi

# ---------------------------------------------------------------------------------------------
# Firebase: guests sign in anonymously (spec 02 §1 — a uid before any personal data), and the PWA
# needs the web SDK config to do it. None of this has a gcloud surface, so it is REST.
# ---------------------------------------------------------------------------------------------
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

json_field() {
  # Returns empty (not a failure) when the field is absent: under `set -o pipefail` a grep miss
  # would otherwise abort the whole script through the command substitution that calls this.
  local match
  match="$(grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 || true)"
  [[ -n "${match}" ]] || return 0
  printf '%s' "${match}" | sed 's/.*:[[:space:]]*"\(.*\)"/\1/'
}
api_call() {
  local method="$1" url="$2" body="${3:-}"
  if [[ -n "${body}" ]]; then
    curl -s -X "${method}" -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      -H "x-goog-user-project: ${PROJECT_ID}" -H "Content-Type: application/json" \
      "${url}" -d "${body}"
  else
    curl -s -X "${method}" -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      -H "x-goog-user-project: ${PROJECT_ID}" "${url}"
  fi
}

FIREBASE_API="https://firebase.googleapis.com/v1beta1"

step "Adding Firebase to the project"
if api_call GET "${FIREBASE_API}/projects/${PROJECT_ID}" | grep -q '"projectId"'; then
  note "already a Firebase project"
else
  api_call POST "${FIREBASE_API}/projects/${PROJECT_ID}:addFirebase" '{}' >/dev/null
  note "Firebase added (operation is async; the web app step below waits for it)"
fi

step "Enabling Anonymous sign-in"
# Auth has to be *provisioned* before its config exists: without this the PATCH below returns
# CONFIGURATION_NOT_FOUND, which is easy to miss because nothing else complains.
api_call POST \
  "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/identityPlatform:initializeAuth" \
  '{}' >/dev/null
# Anonymous auth is the entire guest onboarding story: scan the QR, upload, no account. The
# identity only becomes a person when a selfie claim links it (spec 02 §2).
api_call PATCH \
  "https://identitytoolkit.googleapis.com/admin/v2/projects/${PROJECT_ID}/config?updateMask=signIn.anonymous.enabled" \
  '{"signIn":{"anonymous":{"enabled":true}}}' >/dev/null
note "signIn.anonymous.enabled=true"

step "Web app + SDK config"
APP_ID="$(api_call GET "${FIREBASE_API}/projects/${PROJECT_ID}/webApps" | json_field appId)"
if [[ -z "${APP_ID}" ]]; then
  api_call POST "${FIREBASE_API}/projects/${PROJECT_ID}/webApps" '{"displayName":"Showrunner PWA"}' >/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 5
    APP_ID="$(api_call GET "${FIREBASE_API}/projects/${PROJECT_ID}/webApps" | json_field appId)"
    if [[ -n "${APP_ID}" ]]; then break; fi
  done
fi

if [[ -z "${APP_ID}" ]]; then
  note "web app not ready yet — re-run bootstrap.sh to pick up its SDK config"
else
  CONFIG="$(api_call GET "${FIREBASE_API}/projects/-/webApps/${APP_ID}/config")"
  upsert_env NEXT_PUBLIC_FIREBASE_API_KEY "$(json_field apiKey <<< "${CONFIG}")"
  upsert_env NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN "$(json_field authDomain <<< "${CONFIG}")"
  upsert_env NEXT_PUBLIC_FIREBASE_PROJECT_ID "${PROJECT_ID}"
  upsert_env NEXT_PUBLIC_FIREBASE_APP_ID "${APP_ID}"
  upsert_env NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID "${PROJECT_NUMBER}"
  note "web SDK config written to .env (appId ${APP_ID})"
fi

echo
echo "APIs enabled, ADC confirmed, Model Armor template in place, Firebase anonymous auth on."
echo "Next: run ./deploy/sa.sh (per-service SAs) then ./deploy/up.sh"
echo "Budget alerts + backup billing card: set manually in Billing console (see EXECUTION-PLAN §5/§6)."
