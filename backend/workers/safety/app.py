"""`worker-safety` service — the Cloud Tasks target for the `safety` stage.

The Guardian is the last stage a photo needs before `status='indexed'`, which makes this the last
thing standing between the pipeline and any public surface: until this worker exists, every finished
photo correctly parks at `pool`, because a public-surface query filters on both `visibility` and
`status` (spec 04 §2).

Same order of operations as the other two perception workers, plus one branch:

    claim → load event → chaos gate → fetch render → SafeSearch → [blocked? stop] → dignity → gate

The short-circuit on `blocked` is not an optimisation, or not only one. Spec 03 §5.3 makes
`adult ≥ LIKELY` a hard gate that no model output can lift, so a Gemini call after it could only ever
be ignored — and sending egregious content to a language model to be described in prose is the wrong
instinct anyway. The verdict is decided, the item is forced to the uploader alone, an `ops/` alert
goes up, and the call is never made.

Status codes are the contract with Cloud Tasks: 5xx means retry, 2xx means "retrying will not help".
Every permanent failure returns 200 with the conservative default already written (spec 03 §6) —
which for this stage is `host_review`, never `public_ok` by accident.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from google.api_core import exceptions as gexc

from schemas.common import GuardianVerdict, Stage
from schemas.guardian_out import GuardianOut
from services import gemini, vision
from shared import chaos, fs, gcs, log, pipeline
from shared.settings import settings

from . import gate
from .agent import guardian_agent, prompt_parts

log.configure("worker-safety")

app = FastAPI(title="Showrunner Safety Worker", version="0.1.0", docs_url=None, redoc_url=None)

STAGE = Stage.SAFETY

#: Spec 03 §6's conservative default for this stage, named in the spec itself: `host_review`. The
#: item stays in its uploader's album and the event pool, reaches no public surface, and lands in the
#: host's queue — a degraded item, not a lost one.
CONSERVATIVE_DEFAULT: dict[str, Any] = {
    "guardian.verdict": GuardianVerdict.HOST_REVIEW.value,
    "guardian.reasons": ["stage_failed"],
}


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "worker-safety", "environment": settings().environment}


@app.post("/")
async def on_task(request: Request) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a task body we cannot read will never become readable
        log.warn("task_unparseable")
        return {"ok": True, "skipped": "unparseable_task"}

    event_id = str(payload.get("eventId") or "")
    media_id = str(payload.get("mediaId") or "")
    if not event_id or not media_id:
        log.warn("task_missing_ids", body=str(payload)[:200])
        return {"ok": True, "skipped": "missing_ids"}

    claim = await run_in_threadpool(pipeline.claim_stage, event_id, media_id, STAGE)
    if not claim.ok:
        return {"ok": True, "skipped": claim.outcome}

    event = await run_in_threadpool(fs.get_event, event_id) or {}

    injected = await run_in_threadpool(chaos.should_fail, event_id, STAGE.value, event)
    if injected:
        return await _transient(
            request, claim, event_id, media_id, event, started, injected, gemini.ModelUsage()
        )

    # ---- the render both passes see: classify_768 for photos, the poster for videos until keyframe
    # grids land with `video-prep`. Vision charges per image, not per pixel, so the small render is
    # free of consequence here; a missing render is permanent, the bytes are not coming back.
    media = claim.media
    uri = media.get("classifyUri") or media.get("posterUri") or media.get("displayUri")
    parsed = gcs.parse_gs_uri(str(uri or ""))
    if parsed is None:
        return await _permanent(
            event_id, media_id, event, started, f"no usable render (classifyUri={uri!r})"
        )

    try:
        image = await run_in_threadpool(gcs.download_bytes, parsed[0], parsed[1])
    except gexc.NotFound:
        return await _permanent(event_id, media_id, event, started, "render object missing")
    except Exception as exc:  # noqa: BLE001 - GCS being unhappy is the queue's problem, not ours
        return await _transient(
            request, claim, event_id, media_id, event, started, f"download failed: {exc}",
            gemini.ModelUsage(),
        )

    # ---- pass 1: the hard gate. Deterministic, no prompt, and unappealable.
    try:
        annotation = await run_in_threadpool(vision.safe_search, image)
    except vision.PermanentSafeSearchError as exc:
        return await _permanent(event_id, media_id, event, started, str(exc))
    except vision.TransientSafeSearchError as exc:
        return await _transient(
            request, claim, event_id, media_id, event, started, str(exc), gemini.ModelUsage()
        )

    if gate.safe_search_floor(annotation) is GuardianVerdict.BLOCKED:
        return await _blocked(event_id, media_id, event, started, annotation)

    # ---- pass 2: the judgment. Awaited on the event loop, not in a thread: the GenAI client is
    # bound to the loop it was created on (same constraint as `worker-curate`).
    out: GuardianOut | None = None
    usage = gemini.ModelUsage()
    degraded: str | None = None
    try:
        out, usage = await gemini.run_structured(
            guardian_agent(),
            prompt_parts(event, media, image),
            GuardianOut,
            stage=STAGE.value,
        )
    except gemini.PermanentModelError as exc:
        # Deliberately *not* `_permanent`: spec 03 §5.3 defines this stage's own answer to a refusal
        # — "verdict defaults to host_review" — and that is a completed judgment, not a failed stage.
        # A refusal here is also weak evidence that something about the photo needs a human, so
        # writing the verdict and letting the host see it beats flagging the stage failed and
        # leaving the item with no opinion at all. Recorded in `reasons` either way.
        degraded = str(exc)
        usage = exc.usage
        log.warn("guardian_model_refused", event_id=event_id, media_id=media_id, err=degraded[:200])
    except gemini.ModelError as exc:
        return await _transient(
            request, claim, event_id, media_id, event, started, str(exc), exc.usage
        )

    verdict, reasons = gate.decide(annotation, out, event)
    block: dict[str, Any] = {
        "verdict": verdict.value,
        "reasons": reasons,
        "safeSearch": annotation.as_dict(),
        "ritualEmotion": bool(out.ritualEmotion) if out else False,
        "note": (out.note or None) if out else "the dignity check did not complete",
        "hostDecision": None,
        "decidedBy": None,
    }
    if degraded:
        block["modelError"] = degraded[:300]

    visibility = await run_in_threadpool(
        pipeline.complete_stage,
        event_id,
        media_id,
        STAGE,
        fields={"guardian": block},
        usage=gemini.usage_increments(usage),
        event=event,
        started=started,
        tokens_in=usage.tokensIn,
        tokens_out=usage.tokensOut,
        verdict=verdict.value,
        reasons=",".join(reasons) or None,
    )

    if verdict is GuardianVerdict.HOST_REVIEW:
        # The host console queues off `guardian.verdict` (spec 03 §5.3), so no alert is needed to
        # *find* these — but a silent queue is a queue nobody clears, and the badge is the only thing
        # that tells a host there is a decision waiting.
        await run_in_threadpool(
            fs.ops_alert,
            event_id,
            "guardian_host_review",
            f"a photo needs your call: {', '.join(reasons) or 'no clear reason'}",
            media_id=media_id,
            severity="info",
            stage=STAGE.value,
        )

    return {"ok": True, "verdict": verdict.value, "reasons": reasons, "visibility": visibility}


# ---------------------------------------------------------------- the hard gate's own path


async def _blocked(
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    annotation: vision.SafeSearch,
) -> dict[str, Any]:
    """`adult ≥ LIKELY`: forced to the uploader alone, consent irrelevant, no model call made.

    The stage completes rather than fails — this *is* the answer, arrived at correctly — so the item
    can reach `indexed` with `visibility='self'`. `recompute_visibility` enforces the rest; nothing
    here writes exposure directly.
    """
    verdict, reasons = gate.decide(annotation, None, event)
    visibility = await run_in_threadpool(
        pipeline.complete_stage,
        event_id,
        media_id,
        STAGE,
        fields={
            "guardian": {
                "verdict": verdict.value,
                "reasons": reasons,
                "safeSearch": annotation.as_dict(),
                "note": "blocked by the explicit-content gate; the dignity check was not run",
                "hostDecision": None,
                "decidedBy": None,
            }
        },
        event=event,
        started=started,
        verdict=verdict.value,
    )
    await run_in_threadpool(
        fs.ops_alert,
        event_id,
        "guardian_blocked",
        "explicit content blocked at the SafeSearch gate — uploader-only, needs host attention",
        media_id=media_id,
        severity="error",
        stage=STAGE.value,
    )
    return {"ok": True, "verdict": verdict.value, "visibility": visibility, "modelCalled": False}


# ---------------------------------------------------------------- failure paths


async def _permanent(
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    reason: str,
    usage: gemini.ModelUsage | None = None,
) -> dict[str, Any]:
    """Absorb it here: conservative default (`host_review`), alert, 200 so Tasks stops."""
    defaults = dict(CONSERVATIVE_DEFAULT)
    if usage is not None and (usage.tokensIn or usage.tokensOut):
        defaults.update(gemini.usage_increments(usage))
    await run_in_threadpool(
        pipeline.fail_stage,
        event_id,
        media_id,
        STAGE,
        reason=reason,
        permanent=True,
        defaults=defaults,
        event=event,
        started=started,
    )
    return {"ok": True, "action": "failed_permanent", "reason": reason}


async def _transient(
    request: Request,
    claim: pipeline.Claim,
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    reason: str,
    usage: gemini.ModelUsage,
) -> dict[str, Any]:
    """Hand back to the queue — unless this was the last attempt, which quarantines instead."""
    if usage.tokensIn or usage.tokensOut:
        try:
            await run_in_threadpool(
                fs.media_ref(event_id, media_id).update, gemini.usage_increments(usage)
            )
        except Exception as exc:  # noqa: BLE001 - accounting must not mask the failure
            log.warn("usage_write_failed", event_id=event_id, media_id=media_id, err=str(exc))

    if pipeline.is_last_attempt(claim, request):
        await run_in_threadpool(
            pipeline.fail_stage,
            event_id,
            media_id,
            STAGE,
            reason=f"out of attempts: {reason}",
            permanent=False,
            defaults=dict(CONSERVATIVE_DEFAULT),
            event=event,
            started=started,
            attempts=claim.attempts,
        )
        return {"ok": True, "action": "quarantined", "reason": reason}

    log.stage(
        "retry",
        stage=STAGE.value,
        event_id=event_id,
        media_id=media_id,
        ms=pipeline.elapsed_ms(started),
        attempt=claim.attempts,
        err=reason[:300],
    )
    raise HTTPException(status_code=503, detail=reason[:200])
