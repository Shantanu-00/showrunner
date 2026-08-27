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
  cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  vision.googleapis.com \
  aiplatform.googleapis.com \
  modelarmor.googleapis.com \
  --project "${PROJECT_ID}"
# cloudresourcemanager: not enabled by default on a fresh project, and several gcloud
# subcommands (and the Model Armor call below) fail opaquely without it.
# artifactregistry: `gcloud run deploy --source` needs a repo to push the built image to.

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

echo "APIs enabled, ADC confirmed, Model Armor template in place."
echo "Next: run ./deploy/sa.sh (per-service SAs) then ./deploy/up.sh"
echo "Budget alerts + backup billing card: set manually in Billing console (see EXECUTION-PLAN §5/§6)."
