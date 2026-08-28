#!/usr/bin/env bash
# The `render` Cloud Run **Job** (spec 09 §1: 8 vCPU / 32 GiB, one task per commission). Idempotent.
#
# A Job, not a service, and the difference is not cosmetic: a reel render is two to five minutes of
# 100% CPU on eight cores with no request waiting on it. As a service that is a request holding an
# instance open past every sane timeout; as a Job it is a unit of work with an exit code, an execution
# history on its own console page, and no ingress at all — nothing on the internet can reach it.
#
# Called by up.sh with the shared image tag. Safe to run alone: `gcloud run jobs deploy` creates or
# updates, exactly like `gcloud run deploy` does for a service.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

IMAGE="${1:-}"
if [[ -z "${IMAGE}" ]]; then
  echo "usage: render.sh <image>   (e.g. us-central1-docker.pkg.dev/PROJECT/showrunner/render:TAG)" >&2
  exit 1
fi

RENDER_SA="$(sa_email "${SA_RENDER}")"

# up.sh exports the full config as RENDER_ENV and replaces the job's environment. A standalone
# `make deploy-render` has no such block, so it *updates* instead — the same reason every `deploy-*`
# target in the Makefile uses `--update-env-vars`: a quick redeploy must not silently strip the config
# that up.sh applied.
if [[ -n "${RENDER_ENV:-}" ]]; then
  ENV_FLAG=(--set-env-vars "^;^SERVICE=render;${RENDER_ENV}")
else
  ENV_FLAG=(--update-env-vars "SERVICE=render")
  note "RENDER_ENV unset — updating the job's env rather than replacing it"
fi

step "Deploy the render job (spec 09 §1: 8 vCPU / 32 GiB, task per commission, parallelism 1)"
# --parallelism 1 / --tasks 1: one commission per execution. Concurrency across commissions is the
# per-persona serialisation in directors/reel/store.py, not a dial here — the invariant is "one active
# render per persona per event", which no global number can express.
# --max-retries 0: a failed render is an `ops/` alert and a host-visible failed reel, not something to
# re-run automatically. An 8-vCPU job that retries a deterministic failure three times is a bill.
# --task-timeout 20m: comfortably past the 15-minute ffmpeg ceiling in directors/reel/render.py.
gcloud run jobs deploy render \
  --image "${IMAGE}" --region "${REGION}" --project "${PROJECT_ID}" \
  --service-account "${RENDER_SA}" \
  --cpu 8 --memory 32Gi --parallelism 1 --tasks 1 \
  --max-retries 0 --task-timeout 20m \
  "${ENV_FLAG[@]}" \
  --quiet >/dev/null
note "job render → 8 vCPU / 32Gi as ${RENDER_SA}"

# `api` starts executions of this job (the director's COMMISSION_REEL action, the host's button).
# `run.developer` scoped to the job — not project-wide — is the narrowest grant that permits `run_job`,
# so `api` can start this one job and nothing else in the project. The matching `serviceAccountUser` on
# sa-render is in deploy/sa.sh; both halves are needed and the error when one is missing is opaque.
grant_run_job_invoker render "serviceAccount:$(sa_email "${SA_API}")"

upsert_env RENDER_JOB_NAME "render"
note "RENDER_JOB_NAME=render"
