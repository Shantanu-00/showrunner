"""Cloud Run Jobs dispatch — one execution per reel commission (spec 09 §1's `render` row).

Deliberately shaped like `shared/tasks.py`, because it plays the same role for the one unit of work
that cannot be an HTTP task: **Cloud Tasks speaks HTTP and a Cloud Run Job has no URL.** A job is
started through the Run Admin API, and the per-execution parameters ride in a container *override*
rather than in a request body.

Two consequences worth stating, since they are why the `renders-queue` in spec 09 §2 is not the
throttle it looks like:

- **The serialisation invariant moved to Firestore.** Spec 06 §3 wants commissions serialised per
  persona ("one active render each"); a queue's `max-concurrent=2` is a global dial and cannot
  express "per persona, per event". `directors/reel/commission.py` enforces the real invariant with
  a read of the `reels` collection inside the tick lease that is already held, which is strictly
  stronger. The queue stays configured (nothing else changes) and stays unused by this path.
- **A launch failure is never fatal to its caller.** The caller is the Story Director's ACT step
  inside a tick, or a host pressing a button. Both would rather record the commission and alert than
  fail; the commission document is the durable record, and a later tick can retry it.

Same "unset config logs a skip" rule as `tasks.enqueue`: with no `RENDER_JOB_NAME` deployed, this
returns None rather than raising, so every caller upstream is complete before the job exists.
"""

from __future__ import annotations

import functools

from . import log
from .settings import settings


@functools.lru_cache(maxsize=1)
def _client():
    # Imported lazily: `google-cloud-run` is only needed by the one service that launches renders,
    # and a missing optional dependency must not break every other service's import.
    from google.cloud import run_v2

    return run_v2.JobsClient()


def run_render(event_id: str, reel_id: str) -> str | None:
    """Start one `render` job execution for one reel. Returns the execution name, or None.

    The reel document already holds everything the job needs; the arguments are just the address of
    that document, which keeps the override tiny and means a retry of the same commission reads
    whatever state the previous attempt left behind (`directors/reel/pipeline.py` is resumable at
    stage granularity for exactly this reason).
    """
    cfg = settings()
    if not cfg.render_job or not cfg.project:
        log.info(
            "render_launch_skipped",
            event_id=event_id,
            reel_id=reel_id,
            reason="RENDER_JOB_NAME not configured",
        )
        return None

    from google.cloud import run_v2

    name = f"projects/{cfg.project}/locations/{cfg.location}/jobs/{cfg.render_job}"
    try:
        operation = _client().run_job(
            request=run_v2.RunJobRequest(
                name=name,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            args=["--event", event_id, "--reel", reel_id],
                        )
                    ],
                    task_count=1,
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - classified by the caller, which owns the alert
        log.error("render_launch_failed", event_id=event_id, reel_id=reel_id, err=str(exc))
        raise

    # `run_job` returns an LRO whose metadata carries the execution; we do not wait on it. A render
    # is two to five minutes and the caller is a 30-second tick.
    execution = getattr(operation, "metadata", None)
    execution_name = getattr(execution, "name", "") or ""
    log.info("render_launched", event_id=event_id, reel_id=reel_id, execution=execution_name)
    return execution_name or name
