#!/usr/bin/env bash
# Eventarc: raw-bucket finalize → intake, with a real dead-letter path → dlq. Idempotent.
#
# Run this *after* the Cloud Run services exist (up.sh handles the ordering): a trigger needs a
# destination, and the DLQ push subscription needs the dlq service URL.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TRIGGER="intake-raw-finalized"
EVENTARC_SA="$(sa_email "${SA_EVENTARC}")"
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

INTAKE_URL="$(run_url intake)"
DLQ_URL="$(run_url dlq)"
if [[ -z "${INTAKE_URL}" || -z "${DLQ_URL}" ]]; then
  echo "intake and dlq must be deployed first (run ./deploy/up.sh)." >&2
  exit 1
fi

step "Eventarc service identity"
gcloud beta services identity create --service=eventarc.googleapis.com --project "${PROJECT_ID}" >/dev/null 2>&1 || true
note "eventarc service agent present"

step "Invoker rights for the delivery identity"
for svc in intake dlq; do
  gcloud run services add-iam-policy-binding "${svc}" \
    --region "${REGION}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${EVENTARC_SA}" --role roles/run.invoker >/dev/null
  note "run.invoker on ${svc} → ${EVENTARC_SA}"
done

step "Trigger: storage.object.v1.finalized on gs://${RAW_BUCKET}"
if gcloud eventarc triggers describe "${TRIGGER}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  note "trigger ${TRIGGER} exists"
else
  # A freshly created service account is not immediately visible to Eventarc's validation, which
  # fails with a misleading "service account does not exist". Retry rather than fail the run.
  for attempt in 1 2 3 4 5; do
    if gcloud eventarc triggers create "${TRIGGER}" \
        --location "${REGION}" --project "${PROJECT_ID}" \
        --destination-run-service intake \
        --destination-run-region "${REGION}" \
        --destination-run-path "//" \
        --event-filters "type=google.cloud.storage.object.v1.finalized" \
        --event-filters "bucket=${RAW_BUCKET}" \
        --service-account "${EVENTARC_SA}" >/dev/null 2>&1; then
      note "trigger ${TRIGGER} created"
      break
    fi
    note "trigger creation attempt ${attempt} failed (IAM propagation) — retrying in 20 s"
    sleep 20
    if [[ "${attempt}" == 5 ]]; then
      gcloud eventarc triggers create "${TRIGGER}" \
        --location "${REGION}" --project "${PROJECT_ID}" \
        --destination-run-service intake \
        --destination-run-region "${REGION}" \
        --destination-run-path "//" \
        --event-filters "type=google.cloud.storage.object.v1.finalized" \
        --event-filters "bucket=${RAW_BUCKET}" \
        --service-account "${EVENTARC_SA}"
    fi
  done
fi

step "Dead-letter topic ${DLQ_TOPIC}"
if gcloud pubsub topics describe "${DLQ_TOPIC}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  note "topic ${DLQ_TOPIC} exists"
else
  gcloud pubsub topics create "${DLQ_TOPIC}" --project "${PROJECT_ID}" >/dev/null
  note "topic ${DLQ_TOPIC} created"
fi
# Pub/Sub itself publishes the dead letters and must be able to ack the source subscription.
gcloud pubsub topics add-iam-policy-binding "${DLQ_TOPIC}" \
  --member "serviceAccount:${PUBSUB_AGENT}" --role roles/pubsub.publisher --project "${PROJECT_ID}" >/dev/null
note "pubsub.publisher on ${DLQ_TOPIC} → pubsub service agent"

step "Attaching the DLQ to the trigger's subscription"
# Eventarc owns this subscription; setting a dead-letter policy on it is the supported way to get
# one, since the trigger API exposes no DLQ field of its own.
SUBSCRIPTION="$(gcloud eventarc triggers describe "${TRIGGER}" \
  --location "${REGION}" --project "${PROJECT_ID}" \
  --format='value(transport.pubsub.subscription)')"
if [[ -z "${SUBSCRIPTION}" ]]; then
  echo "could not resolve the trigger's Pub/Sub subscription — check the trigger state" >&2
  exit 1
fi
SUB_ID="${SUBSCRIPTION##*/}"
gcloud pubsub subscriptions add-iam-policy-binding "${SUB_ID}" \
  --member "serviceAccount:${PUBSUB_AGENT}" --role roles/pubsub.subscriber --project "${PROJECT_ID}" >/dev/null
gcloud pubsub subscriptions update "${SUB_ID}" \
  --dead-letter-topic "${DLQ_TOPIC}" \
  --dead-letter-topic-project "${PROJECT_ID}" \
  --max-delivery-attempts 5 \
  --project "${PROJECT_ID}" >/dev/null
note "${SUB_ID}: dead-letters to ${DLQ_TOPIC} after 5 attempts"

step "Push subscription ${DLQ_TOPIC}-push → dlq"
if gcloud pubsub subscriptions describe "${DLQ_TOPIC}-push" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${DLQ_TOPIC}-push" \
    --push-endpoint "${DLQ_URL}/" \
    --push-auth-service-account "${EVENTARC_SA}" \
    --project "${PROJECT_ID}" >/dev/null
  note "push subscription updated → ${DLQ_URL}"
else
  gcloud pubsub subscriptions create "${DLQ_TOPIC}-push" \
    --topic "${DLQ_TOPIC}" \
    --push-endpoint "${DLQ_URL}/" \
    --push-auth-service-account "${EVENTARC_SA}" \
    --ack-deadline 60 \
    --project "${PROJECT_ID}" >/dev/null
  note "push subscription created → ${DLQ_URL}"
fi

echo
echo "Eventarc wired: gs://${RAW_BUCKET} → intake, failures → ${DLQ_TOPIC} → dlq."
