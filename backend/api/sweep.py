"""`POST /internal/sweep` — the hourly reconciliation pass for work that never existed as a task.

Cloud Tasks retries handle a task that exists and fails: five attempts, exponential backoff, then a
dead letter into `dlq`. That machinery is solid and this module does not touch it. What it handles is
the other failure class — a Cloud Run instance killed in the window between a status flip and the
task it implies, a bucket object with no registered intent, a face cluster a racing worker had to
mint rather than serialise against, a public event nobody ever wrapped. Nothing holds a timer for any
of these; nothing will ever look at the document again unless something goes and looks. Five modules
already say so in a comment (`intake/app.py:18,427`, `shared/pipeline.py:102`, `shared/faces.py:223`,
`api/__init__.py:1`) — this is that sweep.

Six cases, in the value order the brief that specified this module ranked them:

- **A1** stranded `pending` stages — `intake/app.py`'s fan-out crashed between the status flip and
  the dispatch, or one queue in a three-way fan-out threw while the other two landed.
- **A2** face-cluster reconciliation — spec 03 §5.2's accepted split-brain: two workers raced the
  same unmatched face and each minted a cluster.
- **A3** orphan raw-bucket objects — bytes with no media doc, the half this job is named after.
- **A4** abandoned upload intents — a signed URL nobody ever used.
- **A5** media stuck at `status=='uploaded'` — the claim landed, nothing after it.
- **A6** the two guardrails spec 11 §1.3/§1.4 configure and nothing enforces: a `public`-class
  event's 60-minute TTL and its $3 cost ceiling.

**Design constraints that shape every case below, not just one of them:**

- *Idempotent.* The job overlaps itself eventually; every case must be safe to run twice.
- *Per-event, and per-case within an event, never fails the sweep.* Copies `api/internal.py::_do_work`'s
  posture — Cloud Scheduler retries a non-2xx, and a retry storm across every event because one had a
  broken document would be worse than the stale state being fixed. This handler always returns 200.
- *Bounded.* Every case has a cap in `shared/settings.py`'s `# --- sweeper ---` block. One enormous
  event must not turn an hourly job into a multi-minute one.
- *Leased.* One `sweeps/global` document, taken at the top and released at the bottom — the same
  primitive `ticks/{eventId}` uses, parameterised the same way (`shared/leases.py`).
- *Alerted.* Every fix this job makes writes an `ops/` alert. A sweeper that silently repairs things
  hides the fact that something upstream produced the mess.
"""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any, Callable

from fastapi import APIRouter, Header, Request
from fastapi.concurrency import run_in_threadpool
from google.cloud.firestore_v1.base_query import FieldFilter

from api import host as host_lifecycle
from api.internal import _issuer, _service_emails
from api.moderation import _routes as _replay_routes
from schemas.common import MediaStatus, Stage, StageState
from schemas.event import EventClass, EventStatus
from shared import errors, fs, gcs, leases, log, oidc, spend, tasks
from shared.auth import Principal
from shared.faces import reconcile_clusters
from shared.ulid import new_ulid
from shared.settings import (
    SIGNED_URL_TTL_MINUTES,
    SWEEP_ABANDONED_SLACK_MINUTES,
    SWEEP_FACE_SCAN_LIMIT,
    SWEEP_LEASE_MINUTES,
    SWEEP_MAX_ACTIONS_PER_CASE,
    SWEEP_MAX_EVENTS_PER_RUN,
    SWEEP_MAX_REDRIVES_PER_RUN,
    SWEEP_MEDIA_SCAN_LIMIT,
    SWEEP_ORPHAN_MIN_AGE_MINUTES,
    SWEEP_ORPHAN_SCAN_LIMIT,
    SWEEP_STRANDED_STAGE_MINUTES,
    SWEEP_STUCK_UPLOAD_MINUTES,
    settings,
)

# `intake.app` bakes `log.configure("intake")` into its module body (it is normally the entrypoint
# for the whole `intake` service, `main.py`), and `_quarantine`/`process` are the two functions A3
# and A5 exist to reuse rather than reinvent. Importing the module is the only way Python hands over
# those two functions, and doing so silently relabels every log line this `api` process ever emits
# again as `service: "intake"`. Restored immediately below; see `docs/context/friction-log.md`.
from intake.app import _quarantine as _intake_quarantine, process as _intake_process  # noqa: E402

log.configure("api")

router = APIRouter(prefix="/internal", tags=["internal"])

#: The identity this module presents to `api/host.py`'s lifecycle machinery when it drives a
#: transition no human requested. `platform_admin=True` is what satisfies `_require_host` for any
#: event without claiming to be that event's actual host — the same shape spec 11 §1.1 already gives
#: the deployment owner's own custom claim, reused here rather than inventing a second "system can
#: always act" escape hatch.
_SWEEPER = Principal(uid="system-sweeper", platform_admin=True)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _sweep_lease_ref() -> Any:
    """`sweeps/global` — a root document, deliberately not `fs.py` plumbing (that module is another
    lane's territory this session): the sweep is platform-wide, not per-event, so it needs no helper
    beyond `fs.db()`, which is already public."""
    return fs.db().collection("sweeps").document("global")


# ---------------------------------------------------------------- auth


def _authorize(request: Request, authorization: str | None) -> str:
    """Service accounts only (`shared/oidc.py`) — reuses `api/internal.py`'s issuer sniff and
    allowlist verbatim, but not its host-token fallback: `/internal/tick`'s fallback is scoped to one
    event a host actually runs, and there is no equivalent scope for a sweep that touches every event,
    so a host token is simply not an identity this endpoint accepts.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized("NO_TOKEN", "this endpoint requires a bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not _issuer(token).endswith("accounts.google.com"):
        raise errors.forbidden("NOT_ALLOWED", "sweep is a service-to-service endpoint")
    try:
        claims = oidc.verify(
            token, allowed_emails=_service_emails(), expected_host=(request.url.hostname or "")
        )
    except oidc.InvalidServiceToken as exc:
        raise errors.forbidden("NOT_ALLOWED", str(exc)) from exc
    return str(claims.get("email") or "service")


# ---------------------------------------------------------------- A1: stranded pending stages


def _replay_stage(
    event_id: str, media_id: str, stage: Stage, route: tuple[str, str], media: dict[str, Any]
) -> None:
    """The body of `api/moderation.py::replay_stage`, minus the host-authorised HTTP wrapper around
    it — that function requires a `Principal`, and there is no host to authorise this replay; the
    system is the one re-running its own stranded work. Same update shape, same enqueue, on purpose:
    a stage replayed by the sweep and a stage replayed by a host from the console must look identical
    to everything downstream.
    """
    queue, url = route
    updates: dict[str, Any] = {
        f"stages.{stage.value}": StageState.PENDING.value,
        f"attempts.{stage.value}": 0,
        f"stageErrors.{stage.value}": fs.DELETE_FIELD,
        f"stageTimings.{stage.value}.queuedAt": fs.SERVER_TIMESTAMP,
    }
    if media.get("status") == MediaStatus.QUARANTINED.value:
        updates["status"] = MediaStatus.PROCESSING.value
    fs.media_ref(event_id, media_id).update(updates)
    tasks.enqueue(
        queue,
        url,
        {"eventId": event_id, "mediaId": media_id, "stage": stage.value, "sweepReplay": True},
        stage=stage.value,
        event_id=event_id,
        media_id=media_id,
    )
    log.info("sweep_stage_requeued", event_id=event_id, media_id=media_id, stage=stage.value)


def _sweep_stranded_stages(event_id: str) -> dict[str, Any]:
    threshold = _now() - dt.timedelta(minutes=SWEEP_STRANDED_STAGE_MINUTES)
    routes = _replay_routes()
    scanned = 0
    requeued = 0
    query = (
        fs.media_col(event_id)
        .where(filter=FieldFilter("status", "==", MediaStatus.PROCESSING.value))
        .limit(SWEEP_MEDIA_SCAN_LIMIT)
    )
    for snap in query.stream():
        scanned += 1
        if requeued >= SWEEP_MAX_ACTIONS_PER_CASE:
            break
        media = snap.to_dict() or {}
        stages = media.get("stages") or {}
        timings = media.get("stageTimings") or {}
        for stage_name, state in stages.items():
            if requeued >= SWEEP_MAX_ACTIONS_PER_CASE:
                break
            if state != StageState.PENDING.value:
                continue
            queued_at = (timings.get(stage_name) or {}).get("queuedAt")
            if not isinstance(queued_at, dt.datetime) or queued_at > threshold:
                continue
            try:
                stage = Stage(stage_name)
            except ValueError:
                continue
            route = routes.get(stage)
            if route is None or not route[1]:
                continue
            _replay_stage(event_id, snap.id, stage, route, media)
            requeued += 1
    if requeued:
        fs.ops_alert(
            event_id,
            "sweep_stranded_stages",
            f"the hourly sweep re-enqueued {requeued} stranded stage(s)",
            severity="warning",
        )
    return {"scanned": scanned, "requeued": requeued}


# ---------------------------------------------------------------- A2: face-cluster reconciliation


def _sweep_clusters(event_id: str) -> dict[str, Any]:
    merges = reconcile_clusters(event_id, scan_limit=SWEEP_FACE_SCAN_LIMIT)
    moved = sum(m.faces_moved for m in merges)
    if merges:
        fs.ops_alert(
            event_id,
            "sweep_clusters_merged",
            f"the hourly sweep merged {len(merges)} duplicate face cluster(s) ({moved} faces)",
            severity="info",
            resolved=True,
        )
    return {"merges": len(merges), "facesMoved": moved}


# ---------------------------------------------------------------- A3: orphan raw-bucket objects


def _sweep_orphans() -> dict[str, Any]:
    cfg = settings()
    if not cfg.raw_bucket:
        return {"scanned": 0, "quarantined": 0}
    threshold = _now() - dt.timedelta(minutes=SWEEP_ORPHAN_MIN_AGE_MINUTES)
    scanned = 0
    quarantined = 0
    for blob in gcs.list_object_names(cfg.raw_bucket, "events/", max_results=SWEEP_ORPHAN_SCAN_LIMIT):
        scanned += 1
        if quarantined >= SWEEP_MAX_ACTIONS_PER_CASE:
            break
        created = blob.time_created
        if created is None or created > threshold:
            continue  # too young to distinguish from the ordinary PUT-then-finalize race
        parsed = gcs.parse_object_path(blob.name)
        if not parsed:
            continue  # a stray outside the event/media path shape is intake's problem, not ours
        event_id, media_id = parsed
        try:
            if fs.media_ref(event_id, media_id).get().exists:
                continue
        except Exception as exc:  # noqa: BLE001 - one bad lookup must not stop the scan
            log.warn("sweep_orphan_lookup_failed", event_id=event_id, media_id=media_id, err=str(exc))
            continue
        try:
            _intake_quarantine(event_id, media_id, cfg.raw_bucket, blob.name)
        except Exception as exc:  # noqa: BLE001
            log.error("sweep_orphan_quarantine_failed", event_id=event_id, media_id=media_id, err=str(exc))
        else:
            quarantined += 1
    return {"scanned": scanned, "quarantined": quarantined}


# ---------------------------------------------------------------- A4: abandoned upload intents


def _sweep_abandoned(event_id: str) -> dict[str, Any]:
    threshold = _now() - dt.timedelta(minutes=SIGNED_URL_TTL_MINUTES + SWEEP_ABANDONED_SLACK_MINUTES)
    scanned = 0
    abandoned = 0
    query = (
        fs.media_col(event_id)
        .where(filter=FieldFilter("status", "==", MediaStatus.AWAITING_UPLOAD.value))
        .limit(SWEEP_MEDIA_SCAN_LIMIT)
    )
    for snap in query.stream():
        scanned += 1
        if abandoned >= SWEEP_MAX_ACTIONS_PER_CASE:
            break
        media = snap.to_dict() or {}
        # `reissuedAt` (a retrying outbox re-registering the same clientMediaId, `api/uploads.py`)
        # is the more recent touch when it exists; falling back to `createdAt` is what makes a
        # never-retried intent age out on its own first signed URL rather than never at all.
        last_touch = media.get("reissuedAt") or media.get("createdAt")
        if not isinstance(last_touch, dt.datetime) or last_touch > threshold:
            continue
        snap.reference.update(
            {"status": MediaStatus.ABANDONED.value, "abandonedAt": fs.SERVER_TIMESTAMP}
        )
        abandoned += 1
    return {"scanned": scanned, "abandoned": abandoned}


# ---------------------------------------------------------------- A5: stuck at `status=='uploaded'`


def _sweep_stuck_uploads(event_id: str) -> dict[str, Any]:
    """Re-drive by re-running `intake.process()` in-process, not by re-enqueueing a Cloud Task:
    intake has no Cloud Tasks target of its own (it is an Eventarc target — `shared/tasks.py` has
    nowhere to send this), and `intake/app.py`'s own `_claim` guard is written to expect exactly this
    replay (`"uploaded" stays claimable on purpose: a transient failure mid-processing must be able
    to resume`). Capped far tighter than the other cases (`SWEEP_MAX_REDRIVES_PER_RUN`): this is real
    GCS download + Pillow decode + re-upload work, not a Firestore write.
    """
    threshold = _now() - dt.timedelta(minutes=SWEEP_STUCK_UPLOAD_MINUTES)
    cfg = settings()
    scanned = 0
    redriven = 0
    query = (
        fs.media_col(event_id)
        .where(filter=FieldFilter("status", "==", MediaStatus.UPLOADED.value))
        .limit(SWEEP_MEDIA_SCAN_LIMIT)
    )
    for snap in query.stream():
        scanned += 1
        if redriven >= min(SWEEP_MAX_ACTIONS_PER_CASE, SWEEP_MAX_REDRIVES_PER_RUN):
            break
        media = snap.to_dict() or {}
        uploaded_at = media.get("uploadedAt")
        object_path = media.get("objectPath")
        if not isinstance(uploaded_at, dt.datetime) or uploaded_at > threshold or not object_path:
            continue
        data: dict[str, Any] = {
            "bucket": cfg.raw_bucket,
            "name": object_path,
            "size": media.get("size") or 0,
            "generation": media.get("objectGeneration") or 0,
            "contentType": media.get("contentType") or "",
        }
        md5_hex = media.get("md5Hash")
        if md5_hex:
            try:
                data["md5Hash"] = base64.b64encode(bytes.fromhex(md5_hex)).decode()
            except ValueError:
                pass
        try:
            _intake_process(data)
        except Exception as exc:  # noqa: BLE001 - one bad redrive must not stop the scan
            log.error("sweep_redrive_failed", event_id=event_id, media_id=snap.id, err=str(exc))
        else:
            redriven += 1
    if redriven:
        fs.ops_alert(
            event_id,
            "sweep_uploads_redriven",
            f"the hourly sweep re-drove {redriven} stalled upload(s) through intake",
            severity="warning",
        )
    return {"scanned": scanned, "redriven": redriven}


# ---------------------------------------------------------------- A6: the two unenforced guardrails


async def _sweep_guardrails(event_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Spec 11 §1.3 (TTL auto-wrap) and §1.4 (cost ceiling), `class=='public'` only — `protected_demo`
    and `internal_dev` are explicitly exempt from both (§1.1's table).

    Drives the real lifecycle machinery in `api/host.py` rather than writing `status` here: cost
    ceiling calls the same read-check-write transition `POST /lifecycle/pause` uses (which is also
    what makes `PAUSED` 403 new signed URLs — `PAUSED` is not in `UPLOAD_OPEN_STATUSES`, so spec
    11 §1.4's "auto-pause uploads" falls out of the existing upload gate for free), and TTL calls the
    exact `wrap` → `finalize` pair a host's own console buttons call, with a synthetic system
    `Principal` standing in for the host who isn't there to press them.

    **Cost ceiling pauses and flags rather than wraps.** Spec 11 §1.4 says "auto-pause uploads … and
    flag for wrap" — not "wrap" — and the brief this module was built from is explicit that the less
    destructive reading wins on any ambiguity. Pausing is reversible (a host can resume); wrapping is
    not. The flag is `costCeilingFlagged` on the event doc, checked here so a repeat sweep does not
    re-pause an already-paused event or spam a second alert.
    """
    if event.get("class") != EventClass.PUBLIC.value:
        return {"skipped": "not_public"}

    cfg = settings()
    result: dict[str, Any] = {}
    status = event.get("status")

    if status == EventStatus.LIVE.value and not event.get("costCeilingFlagged"):
        # Derived, not read off the event document. The sweeper lane correctly flagged that
        # `event.costSoFarUsd` is a schema field nothing has ever incremented, which made this whole
        # branch a permanent no-op — the ceiling could never fire however much an event spent.
        # `shared/spend.py` sums the per-media token counters the workers already write, server-side;
        # it fails closed (returns 0.0 and logs), so an aggregation outage cannot pause a live event.
        cost = spend.usd(event_id)
        if cost > cfg.public_event_cost_ceiling_usd:
            try:
                host_lifecycle._guarded_transition(
                    event_id,
                    expect=EventStatus.LIVE,
                    to=EventStatus.PAUSED,
                    extra={
                        "costCeilingFlagged": True,
                        "costCeilingFlaggedAt": fs.SERVER_TIMESTAMP,
                    },
                )
            except errors.ApiError:
                pass  # raced with a host action; the next sweep re-evaluates from fresh state
            else:
                status = EventStatus.PAUSED.value
                result["costCeiling"] = "paused"
                fs.ops_alert(
                    event_id,
                    "sweep_cost_ceiling",
                    f"public event crossed ${cfg.public_event_cost_ceiling_usd:.2f} "
                    f"(at ${cost:.2f}) — uploads paused, flagged for the host to wrap",
                    severity="warning",
                )

    live_at = event.get("liveAt")
    if not isinstance(live_at, dt.datetime):
        return result
    age_minutes = (_now() - live_at).total_seconds() / 60
    if age_minutes <= cfg.public_event_max_live_minutes:
        return result

    if status in (EventStatus.LIVE.value, EventStatus.PAUSED.value):
        try:
            await host_lifecycle.wrap_event(eventId=event_id, principal=_SWEEPER)
            await host_lifecycle.finalize_event(eventId=event_id, principal=_SWEEPER)
        except errors.ApiError as exc:
            result["ttlError"] = exc.code
        else:
            result["ttl"] = "wrapped"
            fs.ops_alert(
                event_id,
                "sweep_ttl_autowrap",
                f"public event exceeded its {cfg.public_event_max_live_minutes}-minute TTL — "
                "auto-wrapped",
                severity="warning",
            )
    elif status == EventStatus.WRAPPING.value:
        # A previous sweep started the wrap and died before finalizing. Finish it rather than
        # leaving the event stuck holding its capacity slot forever (`wrapping` still holds one).
        try:
            await host_lifecycle.finalize_event(eventId=event_id, principal=_SWEEPER)
        except errors.ApiError as exc:
            result["ttlError"] = exc.code
        else:
            result["ttl"] = "finalized"

    return result


# ---------------------------------------------------------------- orchestration


def _safe(event_id: str, case: str, fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """One case, one event: never lets a broken document in one case take down the others."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - see module docstring's design constraints
        log.error("sweep_case_failed", event_id=event_id, case=case, err=str(exc))
        fs.ops_alert(
            event_id, f"sweep_{case}_failed", f"{case} failed during the sweep: {str(exc)[:300]}",
            severity="error",
        )
        return {"error": str(exc)[:300]}


async def _safe_async(
    event_id: str, case: str, fn: Callable[..., Any], *args: Any
) -> dict[str, Any]:
    try:
        return await fn(*args)
    except Exception as exc:  # noqa: BLE001
        log.error("sweep_case_failed", event_id=event_id, case=case, err=str(exc))
        fs.ops_alert(
            event_id, f"sweep_{case}_failed", f"{case} failed during the sweep: {str(exc)[:300]}",
            severity="error",
        )
        return {"error": str(exc)[:300]}


def _list_events() -> list[tuple[str, dict[str, Any]]]:
    return [
        (snap.id, snap.to_dict() or {})
        for snap in fs.db().collection("events").limit(SWEEP_MAX_EVENTS_PER_RUN).stream()
    ]


async def _sweep_one_event(event_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "strandedStages": await run_in_threadpool(
            _safe, event_id, "stranded_stages", _sweep_stranded_stages, event_id
        ),
        "clusters": await run_in_threadpool(_safe, event_id, "clusters", _sweep_clusters, event_id),
        "abandoned": await run_in_threadpool(_safe, event_id, "abandoned", _sweep_abandoned, event_id),
        "stuckUploads": await run_in_threadpool(
            _safe, event_id, "stuck_uploads", _sweep_stuck_uploads, event_id
        ),
        "guardrails": await _safe_async(event_id, "guardrails", _sweep_guardrails, event_id, event),
    }


@router.post("/sweep")
async def sweep(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    started = _now()
    caller_email = _authorize(request, authorization)

    # A fresh id per invocation, not a constant string — `leases.acquire` treats a repeated holder
    # id as *this same caller renewing*, not a second caller (see its docstring). A constant holder
    # would let a Scheduler retry firing while the previous run is still in flight sail straight
    # through the lease instead of being blocked by it, exactly the failure `ticks/{eventId}` avoids
    # by minting a fresh `tick_id` per tick (`api/internal.py::_tick_one`).
    sweep_id = new_ulid()
    lease = await run_in_threadpool(
        leases.acquire, _sweep_lease_ref(), sweep_id, ttl_seconds=SWEEP_LEASE_MINUTES * 60
    )
    if not lease.ok:
        return {"ok": True, "skipped": "sweep_in_progress"}

    report: dict[str, Any] = {}
    outcome = "ok"
    try:
        report["orphans"] = await run_in_threadpool(_sweep_orphans)
        events = await run_in_threadpool(_list_events)
        report["events"] = [
            await _sweep_one_event(event_id, event) for event_id, event in events
        ]
    except Exception as exc:  # noqa: BLE001 - the sweep itself must still return 200
        outcome = "error"
        report["error"] = str(exc)[:300]
        log.error("sweep_failed", err=str(exc))
    finally:
        await run_in_threadpool(
            leases.release,
            lease,
            lastSweepAt=fs.SERVER_TIMESTAMP,
            lastOutcome=outcome,
            lastCaller=caller_email,
        )

    ms = int((_now() - started).total_seconds() * 1000)
    log.line("sweep", caller=caller_email, events=len(report.get("events") or []), ms=ms)
    return {"ok": True, **report, "ms": ms}
