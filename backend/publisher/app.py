"""`publisher` service — the writer of `kiosk/playlist`, and the only one (spec 04 §4).

The kiosk client is deliberately dumb: it renders the slots it is given and asks no questions. That
puts every decision about what the wall shows in this one service, which is exactly where a judge
should find it — the wall is the most visible surface in the product and the least appropriate place
for a language model to have write access. Agents advise (the Story Director escalates a bounty, the
Reel Director publishes a reel); deterministic code here turns that plus the Curator's stored scores
into a program.

Two things make it a service rather than a Cloud Tasks worker:

- **It listens.** New public photo, stage change, bounty escalation, reel publish — all push. A queue
  worker would need something to enqueue it on every one of those events; a listener needs nothing.
  That is why `min-instances=1` is not a performance choice: scale-to-zero silently kills the
  listener, and the wall would stop updating with no error anywhere.
- **It is a leader, not a singleton.** Per-event leases (spec 04 §4) mean N instances can serve N
  events concurrently while no event ever has two writers.

`POST /recompute` exists for the two moments a listener cannot cover: a deployment scaled to zero
between judge visits, and an instance that has not yet re-acquired a lease it lost. The Cloud
Scheduler tick calls it (via `api`), so the wall is never more than one tick stale — which is also
what lets `scale-down.sh` drop this service to zero for the judging month without the kiosk dying.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from shared import log
from shared.settings import settings

from . import runner
from .supervisor import supervisor

log.configure("publisher")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Leadership is tied to the process: it starts with the instance and is released on SIGTERM.

    Releasing on shutdown rather than letting the lease expire is what makes failover fast — Cloud
    Run gives a terminating instance a grace period, and using it to hand the wall over costs
    nothing and saves the next instance a two-minute wait.
    """
    supervisor.start()
    try:
        yield
    finally:
        supervisor.stop()


app = FastAPI(
    title="Showrunner Publisher",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


class RecomputeRequest(BaseModel):
    eventId: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="manual", max_length=64)


@app.get("/livez")
async def livez() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "publisher",
        "environment": settings().environment,
        "holder": supervisor.holder,
        "events": supervisor.held_events(),
    }


@app.post("/recompute")
async def recompute(req: RecomputeRequest) -> dict[str, Any]:
    """Force one event's program to be rebuilt now. Private (IAM: only `sa-api` may call it)."""
    result = await run_in_threadpool(
        runner.recompute, req.eventId, holder=supervisor.holder, trigger=f"nudge:{req.reason}"
    )
    return result
