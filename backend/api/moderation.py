"""Host moderation and surgical replay — the two human levers on the perception pipeline.

Both endpoints exist because the pipeline is allowed to be wrong. The Guardian routes what it cannot
judge to `host_review` (spec 03 §5.3) and a transient-exhausted stage quarantines an item (spec 03
§6); neither is a dead end, because a host can overrule the first and re-run the second.

The shape of the override is the part worth reading closely. A host decision does **not** write
`visibility`, and it does not overwrite the model's verdict either — it writes
`guardian.hostDecision`, and `recompute_visibility` prefers that field over `guardian.verdict` when
it decides exposure (spec 04 §2). Three properties follow, all of them things the trust architecture
promises elsewhere:

- there is still exactly one writer of `visibility`, so the grep-level check in spec 04 §6 holds;
- the model's original verdict survives next to the human's, which is what makes the decision
  *audited* rather than simply applied — `eval/` compares the two, and a host who overrides the
  Guardian ninety times is telling us the rubric is miscalibrated;
- a later consent change, subject veto or re-render recomputes from the same inputs and reaches the
  same answer, because the host's decision is an input and not a one-off mutation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from schemas.common import GuardianVerdict, MediaStatus, Stage, StageState
from schemas.moderation import ReplayResponse, ReviewDecision, ReviewResponse
from shared import errors, fs, log, tasks
from shared.auth import Principal, caller
from shared.settings import settings
from shared.visibility import recompute_visibility

router = APIRouter(prefix="/v1/events/{eventId}", tags=["moderation"])

#: Which queue and target service each replayable stage belongs to. `thumb` is absent on purpose:
#: intake's renders are not a Cloud Tasks stage, and re-running them means replaying the object
#: finalize event, not a task (spec 01 §5).
_STAGE_ROUTES: dict[Stage, tuple[str, str]] = {}


def _routes() -> dict[Stage, tuple[str, str]]:
    """Resolved lazily: the worker URLs come from the environment at request time, not import time."""
    cfg = settings()
    return {
        Stage.CURATE: (cfg.classify_queue, cfg.curate_url),
        Stage.FACES: (cfg.face_queue, cfg.face_url),
        Stage.SAFETY: (cfg.safety_queue, cfg.safety_url),
        Stage.VIDEO_PREP: (cfg.video_prep_queue, cfg.video_prep_url),
    }


def _require_host(principal: Principal, event_id: str) -> None:
    if not (principal.is_host_of(event_id) or principal.platform_admin):
        raise errors.forbidden("HOST_ONLY", "this action requires the host")


def _media(event_id: str, media_id: str) -> dict[str, Any]:
    snap = fs.media_ref(event_id, media_id).get()
    if not snap.exists:
        raise errors.not_found("NO_MEDIA", "unknown media")
    return snap.to_dict() or {}


@router.post("/media/{mediaId}/review", response_model=ReviewResponse)
async def review_media(
    req: ReviewDecision,
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> ReviewResponse:
    """Host decides a `host_review` photo (spec 03 §5.3: "host decision overwrites verdict, audited")."""
    _require_host(principal, eventId)
    media = _media(eventId, mediaId)
    guardian = media.get("guardian") or {}

    if req.decision is GuardianVerdict.BLOCKED:
        raise errors.bad_request(
            "NOT_A_HOST_VERDICT",
            "`blocked` is the explicit-content gate's verdict, not a moderation decision",
        )

    # A `blocked` item is not up for review. The gate that produced it is the one thing in this
    # system a human cannot argue with from a console — deleting it is the available action.
    if guardian.get("verdict") == GuardianVerdict.BLOCKED.value:
        raise errors.conflict(
            "BLOCKED_AT_GATE", "this item was blocked by the explicit-content gate and cannot be released"
        )

    updates: dict[str, Any] = {
        "guardian.hostDecision": req.decision.value,
        "guardian.decidedBy": principal.uid,
        "guardian.decidedAt": fs.SERVER_TIMESTAMP,
    }
    if req.note:
        updates["guardian.hostNote"] = req.note

    # `extra` rides inside `recompute_visibility`'s transaction, so the decision and the exposure it
    # implies land in one write — never a window where the host's call is stored and unenforced.
    visibility = recompute_visibility(eventId, mediaId, extra=updates)

    fs.ops_alert(
        eventId,
        "moderation_decision",
        f"host set {req.decision.value} (model said {guardian.get('verdict') or 'nothing'})",
        media_id=mediaId,
        severity="info",
        # Written already-resolved: the host console lists open alerts with
        # `where('resolved','==',false)`, and a record of a decision the host just made needs no
        # action from them. It belongs in the activity feed, not on the badge.
        resolved=True,
        decidedBy=principal.uid,
        modelVerdict=guardian.get("verdict"),
        hostDecision=req.decision.value,
    )
    log.info(
        "moderation_decision",
        event_id=eventId,
        media_id=mediaId,
        host=principal.uid,
        decision=req.decision.value,
        model_verdict=guardian.get("verdict"),
        visibility=visibility,
    )
    return ReviewResponse(mediaId=mediaId, verdict=req.decision, visibility=visibility)


@router.post("/admin/replay/{mediaId}", response_model=ReplayResponse)
async def replay_stage(
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    stage: Stage = Query(description="the one stage to re-run"),
    principal: Principal = Depends(caller),
) -> ReplayResponse:
    """Re-enqueue exactly one stage (spec 03 §6's `/v1/admin/replay/{mediaId}?stage=`).

    Event-scoped rather than global, because every path in this system is (spec 03 §1) and a replay
    authorised by a host claim has to name the event that claim is for.

    The stage flag is reset to `pending` and its attempt counter cleared *before* the dispatch, so the
    worker's claim transaction sees a runnable stage — `pipeline.claim_stage` refuses a stage that is
    already `done` or `failed_permanent`, which is exactly the guard that makes an ordinary duplicate
    delivery free and would otherwise make a replay a no-op. Clearing `attempts` is what gives the
    replay a full five tries of its own instead of inheriting an exhausted budget.
    """
    _require_host(principal, eventId)
    media = _media(eventId, mediaId)

    route = _routes().get(stage)
    if route is None:
        raise errors.bad_request("NOT_REPLAYABLE", f"{stage.value} is not a Cloud Tasks stage")
    queue, url = route
    if not url:
        raise errors.conflict("NO_WORKER", f"no worker deployed for {stage.value}")
    if stage.value not in (media.get("stages") or {}):
        raise errors.bad_request(
            "STAGE_NOT_ON_ITEM", f"this {media.get('kind')} has no {stage.value} stage"
        )

    updates: dict[str, Any] = {
        f"stages.{stage.value}": StageState.PENDING.value,
        f"attempts.{stage.value}": 0,
        f"stageErrors.{stage.value}": fs.DELETE_FIELD,
        f"stageTimings.{stage.value}.queuedAt": fs.SERVER_TIMESTAMP,
    }
    # A quarantine is the state a replay exists to leave: put the item back in flight so the derived
    # `status='indexed'` can be reached again once every stage lands (shared/pipeline.py).
    if media.get("status") == MediaStatus.QUARANTINED.value:
        updates["status"] = MediaStatus.PROCESSING.value

    fs.media_ref(eventId, mediaId).update(updates)
    tasks.enqueue(
        queue,
        url,
        {"eventId": eventId, "mediaId": mediaId, "stage": stage.value, "replay": True},
        stage=stage.value,
        event_id=eventId,
        media_id=mediaId,
    )
    log.info(
        "stage_replayed", event_id=eventId, media_id=mediaId, stage=stage.value, by=principal.uid
    )
    return ReplayResponse(
        mediaId=mediaId,
        stage=stage,
        queued=True,
        status=str(updates.get("status") or media.get("status") or ""),
    )
