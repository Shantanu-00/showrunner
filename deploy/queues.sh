#!/usr/bin/env bash
# The six Cloud Tasks queues. Rates are spec 09 §2 verbatim — they are the spend throttle in
# front of every paid model call, not a performance knob. Idempotent (create, else update).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# name : dispatches-per-second : max-concurrent
QUEUES=(
  "${CLASSIFY_QUEUE:-classify-queue}:8:10"
  "${FACE_QUEUE:-face-queue}:10:8"
  "${SAFETY_QUEUE:-safety-queue}:8:10"
  "${VIDEO_PREP_QUEUE:-video-prep-queue}:2:2"
  "${PRIORITY_QUEUE:-priority-queue}:20:10"
  "${RENDERS_QUEUE:-renders-queue}:1:2"
)

# Retry policy is uniform (spec 03 §6): transient failures get 5 attempts with 10 s→300 s backoff;
# permanent failures return 200 from the handler so they never reach this policy at all.
RETRY_ARGS=(
  --max-attempts=5
  --min-backoff=10s
  --max-backoff=300s
  --max-doublings=4
)

step "Cloud Tasks queues in ${REGION}"
for entry in "${QUEUES[@]}"; do
  IFS=':' read -r name rate concurrent <<< "${entry}"
  if gcloud tasks queues describe "${name}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud tasks queues update "${name}" \
      --location "${REGION}" --project "${PROJECT_ID}" \
      --max-dispatches-per-second="${rate}" \
      --max-concurrent-dispatches="${concurrent}" \
      "${RETRY_ARGS[@]}" >/dev/null
    note "${name} updated (${rate}/s, ${concurrent} concurrent)"
  else
    gcloud tasks queues create "${name}" \
      --location "${REGION}" --project "${PROJECT_ID}" \
      --max-dispatches-per-second="${rate}" \
      --max-concurrent-dispatches="${concurrent}" \
      "${RETRY_ARGS[@]}" >/dev/null
    note "${name} created (${rate}/s, ${concurrent} concurrent)"
  fi
  # A queue can be paused by the demo kill-switch; make sure a re-run un-pauses it.
  gcloud tasks queues resume "${name}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1 || true
done

step "Recording queue names in .env"
upsert_env CLASSIFY_QUEUE "${CLASSIFY_QUEUE:-classify-queue}"
upsert_env FACE_QUEUE "${FACE_QUEUE:-face-queue}"
upsert_env SAFETY_QUEUE "${SAFETY_QUEUE:-safety-queue}"
upsert_env VIDEO_PREP_QUEUE "${VIDEO_PREP_QUEUE:-video-prep-queue}"
upsert_env PRIORITY_QUEUE "${PRIORITY_QUEUE:-priority-queue}"
upsert_env RENDERS_QUEUE "${RENDERS_QUEUE:-renders-queue}"

echo
echo "Queues ready. Rates are configuration: a Tier-2 spend upgrade is one \`queues update\`."
