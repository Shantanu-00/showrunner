#!/usr/bin/env bash
# The judging-month posture: Sep 1 → Oct 1, cheap but genuinely working.
#
# This is NOT scale-down.sh, and the difference is the reason this file exists.
# `scale-down.sh` is a between-sessions cost brake: it **pauses every Cloud Tasks queue**, which is
# correct when nobody is using the system and catastrophic when a judge is. A paused classify queue
# means an uploaded photo sits at `stages={'thumb': ...}` for ever — never classified, never
# face-indexed, never safety-screened, so it never reaches `status='indexed'` and therefore never
# reaches any public surface. The guest filmstrip reads "The Curator is judging your shot…"
# indefinitely. No error, no alert, no log: the most expensive possible failure during the one month
# the rules require the project to stay testable.
#
# So: queues stay running, the two services that hold state stay warm, and the money is saved on the
# director's *cadence* instead — which is where it actually is.
#
#   ./deploy/demo-mode.sh          # enter judging posture
#   ./deploy/up.sh                  # back to full demo posture (restores */2 and the demo job)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

JUDGE_TICK_SCHEDULE="${JUDGE_TICK_SCHEDULE:-*/15 * * * *}"

step "Director cadence: ${JUDGE_TICK_SCHEDULE} (from */2)"
# The arithmetic this is answering (HANDOFF §9): `director-tick` at 2 minutes is 720 ticks/day, and
# from B3-S8b every tick is a `gemini-3.7-flash` call — roughly $1.4/day, ≈$43 across the judging
# month, out of a $150 credit grant that also has to cover everything else. At */15 that is ≈$6.
#
# What a judge loses is only the *wait*: the tick is still a real unprompted Cloud Scheduler
# invocation, and the tour countdown shows the true schedule. This is exactly why that page needs
# both the countdown and a clearly-labelled manual override — a 15-minute cadence without an escape
# hatch is dead air, and an override without a visible schedule beside it is a button pretending to be
# autonomy.
#
# NOTE: `shared/settings.py::PRODUCTION_TICK_SECONDS` is what the countdown divides by. If you change
# JUDGE_TICK_SCHEDULE, change that too, or the countdown lies.
if gcloud scheduler jobs describe director-tick --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http director-tick \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --schedule "${JUDGE_TICK_SCHEDULE}" --quiet >/dev/null
  note "director-tick: ${JUDGE_TICK_SCHEDULE}"
else
  note "director-tick not found — run ./deploy/up.sh first"
fi

step "Pausing the 30-second demo cadence"
# `director-tick-demo` fires every minute and interleaves a +30 s task: 1,440 director calls a day,
# which is right for four minutes of filming and wrong for five weeks of judging. The production job
# above already walks every live event regardless of class, so the demo event keeps being ticked.
if gcloud scheduler jobs describe director-tick-demo --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud scheduler jobs pause director-tick-demo --location "${REGION}" --project "${PROJECT_ID}" --quiet >/dev/null
  note "director-tick-demo: paused"
fi

step "Queues: confirming every one is RUNNING (the scale-down.sh trap)"
for q in "${CLASSIFY_QUEUE:-classify-queue}" "${FACE_QUEUE:-face-queue}" "${SAFETY_QUEUE:-safety-queue}" \
         "${VIDEO_PREP_QUEUE:-video-prep-queue}" "${PRIORITY_QUEUE:-priority-queue}" "${RENDERS_QUEUE:-renders-queue}"; do
  if gcloud tasks queues describe "${q}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    state="$(gcloud tasks queues describe "${q}" --location "${REGION}" --project "${PROJECT_ID}" --format='value(state)')"
    if [[ "${state}" != "RUNNING" ]]; then
      gcloud tasks queues resume "${q}" --location "${REGION}" --project "${PROJECT_ID}" --quiet >/dev/null
      note "${q}: was ${state} → resumed"
    else
      note "${q}: RUNNING"
    fi
  fi
done

step "Service scaling (spec 09 §1 + README-PLAN Part B)"
# `api` min-1: the first judge click hits it, and a cold FastAPI start in front of a judge is the one
# latency nobody absorbs for you. ~$5–8/month.
gcloud run services update api --region "${REGION}" --project "${PROJECT_ID}" \
  --min-instances 1 --quiet >/dev/null && note "api: min-instances=1"

# `publisher` min-1 AND --no-cpu-throttling, both of them, because either alone is insufficient: this
# service's job is holding Firestore listeners on a background thread, min-0 kills the listener
# outright and CPU-throttled freezes it between requests. Silent either way (B3-S8a).
gcloud run services update publisher --region "${REGION}" --project "${PROJECT_ID}" \
  --min-instances 1 --no-cpu-throttling --quiet >/dev/null && note "publisher: min-instances=1, no-cpu-throttling"

# Everything else scales to zero. `worker-face` is the one that hurts (~29.6 s cold, 326 MB model) and
# its answer is `GET /warmup`, which the tour page fires on load — by the time a visitor has read the
# tour and tapped upload, the container is warm. min-instances=1 (~$15/mo) is the fallback if the hook
# proves insufficient in the weekly deep check.
for svc in intake dlq worker-curate worker-face worker-safety worker-video-prep; do
  if gcloud run services describe "${svc}" --region "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud run services update "${svc}" --region "${REGION}" --project "${PROJECT_ID}" \
      --min-instances 0 --quiet >/dev/null
    note "${svc}: min-instances=0"
  fi
done

step "Re-enabling public event creation (spec 11 §1.5's kill switch)"
# Spec 11 §1.3/§1.4 design a 60-minute auto-wrap and a $3 per-event cost ceiling for stranger-created
# events. Both are now enforced by the hourly `orphan-sweep` (`backend/api/sweep.py`, wired by
# `deploy/scheduler.sh`), on top of the transactional capacity cap that was already running — so the
# guardrail this flag used to stand in for is live, and public Go Live no longer needs to stay off
# for the judging period. `class=='protected_demo'` was never affected either way; the
# /how-it-works disclosure panel reflects the current posture, not the old one.
python - "${PROJECT_ID}" <<'PY'
import sys
from google.cloud import firestore

db = firestore.Client(project=sys.argv[1])
db.collection("platform").document("publicCreationEnabled").set(
    {"enabled": True, "reason": "orphan-sweep enforces the public-event TTL and cost ceiling"},
    merge=True,
)
print("   platform/publicCreationEnabled: enabled=true")
PY

step "Verify"
for job in director-tick director-tick-demo orphan-sweep; do
  gcloud scheduler jobs describe "${job}" --location "${REGION}" --project "${PROJECT_ID}" \
    --format='value[separator="  ·  "](name.basename(),state,schedule)' 2>/dev/null | sed 's/^/   /'
done

cat <<SUMMARY

Judging posture set. Queues RUNNING, api + publisher warm, director every 15 minutes.
orphan-sweep left running hourly — it's what makes public event creation safe to leave on.
Idle cost ≈ \$0.50–0.90/day.

Still owed by hand (README-PLAN Part B):
  · UptimeRobot on /how-it-works and {api}/livez
  · scripts/seed_global_event.py — one-time creation, no nightly reseed needed (no fixture photos)
  · the daily 5-minute ritual, incl. a glance at platform/liveEventCount

Undo: ./deploy/up.sh  (restores */2, resumes director-tick-demo, re-warms everything)
Disable public creation by hand if abuse shows up: set platform/publicCreationEnabled.enabled = false
SUMMARY
