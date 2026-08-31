#!/usr/bin/env bash
# Cost brake between work sessions: drop every service to min-instances=0 and pause the queues.
#
# ** This is NOT the judging-month posture — use deploy/demo-mode.sh for that. **
# Pausing the queues is right when nobody is using the system and silently fatal when someone is:
# an uploaded photo stops at `stages={'thumb': ...}`, never reaches `status='indexed'`, and so never
# reaches any public surface, while the guest's filmstrip reads "The Curator is judging your shot..."
# for ever. No error and no alert anywhere. Likewise `publisher` at min-instances=0 loses the
# Firestore listener that keeps the wall fresh (see the note at the bottom of this file).
#
# Between work sessions and after a demo this is the difference between a few cents and a few
# dollars an hour. `up.sh` (and queues.sh) put everything back.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

step "Pausing the demo-cadence Scheduler job"
# `director-tick-demo` fires every minute and interleaves a +30 s task, which is right for four
# minutes of filming and wrong for five weeks of judging: 1,440 ticks a day against a Gemini-calling
# director is real money. `director-tick` stays running at 2 min and already covers the demo event
# (it walks *every* live event regardless of class), which is exactly spec 09 §4's post-submission
# posture — "scheduler paused except a slow demo-event tick". So a judge who visits still watches the
# fleet act unprompted; it just reconciles every two minutes instead of every thirty seconds.
if gcloud scheduler jobs describe director-tick-demo --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud scheduler jobs pause director-tick-demo --location "${REGION}" --project "${PROJECT_ID}" --quiet >/dev/null
  note 'director-tick-demo: paused (gcloud scheduler jobs resume director-tick-demo to bring it back)'
fi

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
echo "The publisher's Firestore listener stops with its last instance; the 2-min director tick"
echo "nudges it over HTTP instead, so the kiosk still refreshes (one cold start per tick)."
