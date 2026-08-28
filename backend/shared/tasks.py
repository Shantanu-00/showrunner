"""Cloud Tasks dispatch — the single throttle in front of every paid perception call.

Two decisions are already made and must not be re-litigated (spec 01 §5, spec 03 §6):

- **Tasks are unnamed.** Named tasks look like free dedupe but add dispatch latency and a task
  name cannot be reused for hours after completion, which would break the replay endpoint.
  Idempotency lives in the handlers' status transactions instead.
- **Rates are configuration, not code** (spec 09 §2) — set once in `deploy/queues.sh`.

Until the B2 workers exist their target URLs are unset; `enqueue` then logs a skipped dispatch
and returns None rather than queueing work nothing can consume. Intake's fan-out is complete
now and becomes live the moment a URL lands in the environment.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
from typing import Any

from google.cloud import tasks_v2

from . import log
from .settings import settings


@functools.lru_cache(maxsize=1)
def client() -> tasks_v2.CloudTasksClient:
    return tasks_v2.CloudTasksClient()


def enqueue(
    queue: str,
    target_url: str,
    payload: dict[str, Any],
    *,
    stage: str | None = None,
    event_id: str | None = None,
    media_id: str | None = None,
    schedule_in_seconds: float | None = None,
    audience: str | None = None,
) -> str | None:
    """Queue one unnamed HTTP task carrying a JSON body, authenticated with an OIDC token.

    `schedule_in_seconds` delays first delivery — Cloud Tasks is the only sub-minute timer available
    (Cloud Scheduler's cron floor is 1 minute), which is exactly how spec 09 §2 gets a 30-second
    demo cadence out of a `* * * * *` job. `audience` overrides the OIDC audience: it defaults to
    the target URL, which is right for a worker but wrong when the target carries a query string,
    since the receiving service verifies the audience against its own host.
    """
    cfg = settings()
    if not target_url:
        log.info(
            "dispatch_skipped",
            queue=queue,
            stage=stage,
            event_id=event_id,
            media_id=media_id,
            reason="target url not configured",
        )
        return None

    request: dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": target_url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode(),
        }
    }
    if cfg.tasks_sa_email:
        request["http_request"]["oidc_token"] = {
            "service_account_email": cfg.tasks_sa_email,
            "audience": audience or target_url,
        }
    if schedule_in_seconds is not None:
        request["schedule_time"] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=schedule_in_seconds
        )

    task = client().create_task(parent=cfg.queue_path(queue), task=request)
    log.info(
        "dispatched",
        queue=queue,
        stage=stage,
        event_id=event_id,
        media_id=media_id,
        delay_s=schedule_in_seconds,
        task=task.name.rsplit("/", 1)[-1],
    )
    return task.name
