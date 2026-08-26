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

echo "Confirming Application Default Credentials..."
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "ADC missing — run: gcloud auth application-default login"
  exit 1
fi

echo "APIs enabled, ADC confirmed."
echo "Next: run ./deploy/sa.sh (per-service SAs) then ./deploy/up.sh"
echo "Budget alerts + backup billing card: set manually in Billing console (see EXECUTION-PLAN §5/§6)."
