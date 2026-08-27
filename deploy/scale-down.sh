#!/usr/bin/env bash
# Cost brake: drop every service to min-instances=0 and pause the queues.
#
# Between work sessions and after a demo this is the difference between a few cents and a few
# dollars an hour. `up.sh` (and queues.sh) put everything back.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

step "Scaling services to zero"
for svc in api intake dlq worker-curate worker-face worker-safety worker-video-prep publisher; do
  if gcloud run services describe "${svc}" --region "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud run services update "${svc}" \
      --region "${REGION}" --project "${PROJECT_ID}" --min-instances 0 --quiet >/dev/null
    note "${svc}: min-instances=0"
  fi
done

step "Pausing queues"
for q in "${CLASSIFY_QUEUE:-classify-queue}" "${FACE_QUEUE:-face-queue}" "${SAFETY_QUEUE:-safety-queue}" \
         "${VIDEO_PREP_QUEUE:-video-prep-queue}" "${PRIORITY_QUEUE:-priority-queue}" "${RENDERS_QUEUE:-renders-queue}"; do
  if gcloud tasks queues describe "${q}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud tasks queues pause "${q}" --location "${REGION}" --project "${PROJECT_ID}" --quiet >/dev/null
    note "${q}: paused"
  fi
done

echo
echo "Scaled down. Queued tasks are held, not dropped — ./deploy/queues.sh resumes them."
