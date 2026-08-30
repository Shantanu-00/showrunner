"""`worker-curate` service — the Cloud Tasks target for the `curate` stage.

The handler is small on purpose; everything that is not "look at this photo" lives elsewhere.
`shared.pipeline` owns claim/complete/fail, `services.gemini` owns the model call and the
transient-vs-permanent classification, `agent.py` owns the prompt and `fusion.py` owns the
deterministic part. What is left here is the order of operations, and that order is the design:

    claim → load event → chaos gate → fetch render → model → fuse → commit

Two things are deliberate about it. The claim happens **before** the download and the model call,
so a duplicate delivery, a deleted item or a dedupe loser costs one Firestore read rather than a
Gemini call — dedupe only saves money if the saving happens before the spend. And the chaos gate
sits after the claim but before the download, so an injected failure exercises the retry path
without paying for bytes it is going to throw away.

Status codes are the contract with Cloud Tasks: 5xx means "retry this", 2xx means "done, in the
sense that retrying will not help". Every permanent failure therefore returns 200 with a
conservative default already written (spec 03 §6) — a poisoned photo costs one pass, not a storm.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from google.api_core import exceptions as gexc

from schemas.common import SceneSetting, Stage
from schemas.curator_out import CuratorOut
from schemas.media import CuratorBlock, Quality
from services import gemini
from shared import chaos, fs, gcs, log, pipeline
from shared.settings import settings

from . import fusion
from .agent import curator_agent, prompt_parts

log.configure("worker-curate")

app = FastAPI(title="Showrunner Curate Worker", version="0.1.0", docs_url=None, redoc_url=None)

STAGE = Stage.CURATE

#: Spec 03 §6's conservative default for this stage. Score 0 keeps the item under every
#: `publicFloor`, and `needsReview` is what puts it in the host's review queue instead of silently
#: leaving it looking like an unremarkable photo.
CONSERVATIVE_DEFAULT: dict[str, Any] = {
    "curator.aestheticScore": 0.0,
    "curator.isHighlight": False,
    "curator.needsReview": True,
}


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "worker-curate", "environment": settings().environment}


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

    # ---- the render Gemini sees: classify_768 for photos, the poster for videos until keyframe
    # grids land with `video-prep`. A missing render is permanent — the bytes are not coming back.
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
        # Deleted between intake and now: a real outcome (a guest deleting their upload), not a bug.
        return await _permanent(event_id, media_id, event, started, "render object missing")
    except Exception as exc:  # noqa: BLE001 - GCS being unhappy is the queue's problem, not ours
        return await _transient(
            request, claim, event_id, media_id, event, started, f"download failed: {exc}",
            gemini.ModelUsage(),
        )

    # ---- the one paid call. Awaited on the event loop rather than pushed to a thread: the GenAI
    # client is bound to the loop it was created on, and running it in a worker thread closes it
    # out from under the next request.
    try:
        out, usage = await gemini.run_structured(
            curator_agent(),
            prompt_parts(event, image),
            CuratorOut,
            stage=STAGE.value,
        )
    except gemini.PermanentModelError as exc:
        return await _permanent(event_id, media_id, event, started, str(exc), exc.usage)
    except gemini.ModelError as exc:
        return await _transient(
            request, claim, event_id, media_id, event, started, str(exc), exc.usage
        )

    block = _fuse(out, media, event)
    visibility = await run_in_threadpool(
        pipeline.complete_stage,
        event_id,
        media_id,
        STAGE,
        fields={"curator": block.model_dump(mode="json")},
        usage=gemini.usage_increments(usage),
        event=event,
        started=started,
        tokens_in=usage.tokensIn,
        tokens_out=usage.tokensOut,
        stage_id=block.stageId,
        aesthetic=round(block.aestheticScore, 2),
        highlight=block.isHighlight,
    )
    return {
        "ok": True,
        "stageId": block.stageId,
        "aestheticScore": block.aestheticScore,
        "visibility": visibility,
    }


# ---------------------------------------------------------------- fusion glue


def _fuse(out: CuratorOut, media: dict[str, Any], event: dict[str, Any]) -> CuratorBlock:
    """Turn the model's opinion into the stored block. This is where the LLM stops being in charge.

    The raw `visual` distribution is stored next to the fused posterior on purpose (spec 03 §5.1):
    the Story Director compares them, and a confident visual answer that disagrees with the
    schedule means the event is running off its timetable, not that the photo is mislabelled.
    """
    visual = {entry.stageId: entry.score for entry in out.visual if entry.stageId}
    stage_id, posterior = fusion.fuse(
        visual,
        list(event.get("stages") or []),
        media.get("capturedAt"),
        exif_missing=bool(media.get("exifMissing")),
    )
    return CuratorBlock(
        stageId=stage_id,
        stagePosterior=posterior,
        visual=visual,
        momentTags=out.momentTags,
        aestheticScore=out.aestheticScore,
        quality=Quality(
            blur=out.quality.blur,
            exposure=out.quality.exposure,
            eyesClosed=out.quality.eyesClosed,
        ),
        isHighlight=out.isHighlight,
        caption=out.caption or None,
        culturalElements=_glossary_filter(out.culturalElements, event),
        peopleCountEstimate=out.peopleCountEstimate,
        sceneSetting=_scene_setting(out.sceneSetting),
        needsReview=False,
    )


def _scene_setting(raw: str) -> SceneSetting:
    """Coerce the model's string to the closed vocabulary; anything else becomes `unknown`.

    Same posture as `_glossary_filter` below and for the same reason: the instruction lists the nine
    values, but "the model was told to" is not a guarantee, and here the cost of a stray value is
    specific — every scene tag becomes a Firestore map key on a coverage shard
    (`shared/coverage.py::bump`), so an invented setting would silently create a new bucket and quietly
    dilute the distribution the world model reasons from. Falling back to `unknown` is safe because
    `unknown` is already defined as "no information", not as a low score.
    """
    candidate = (raw or "").strip().lower()
    try:
        return SceneSetting(candidate)
    except ValueError:
        if candidate:
            log.warn("scene_setting_dropped", offered=candidate[:64])
        return SceneSetting.UNKNOWN


def _glossary_filter(elements: list[str], event: dict[str, Any]) -> list[str]:
    """Drop any cultural term the host did not review (spec 11 §2).

    The prompt already forbids inventing one. This enforces it, because "the model was told not to"
    is not a guarantee, and a hallucinated ritual name attached to a stranger's wedding photo is
    the kind of mistake that cannot be walked back with an apology.
    """
    profile = event.get("eventTypeProfile") or {}
    allowed = {str(term).strip().lower() for term in (profile.get("culturalGlossary") or [])}
    if not allowed:
        return []
    kept = [term for term in elements if str(term).strip().lower() in allowed]
    dropped = len(elements) - len(kept)
    if dropped:
        log.warn("cultural_elements_dropped", count=dropped, offered=",".join(elements)[:200])
    return kept


# ---------------------------------------------------------------- failure paths


async def _permanent(
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    reason: str,
    usage: gemini.ModelUsage | None = None,
) -> dict[str, Any]:
    """Absorb the failure here: conservative default, alert, 200 so Tasks stops."""
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
    """Hand back to the queue — unless this was the last attempt, in which case it stops here.

    Tokens already spent are recorded even on a retry. A cost ticker that only counts successful
    calls understates the bill precisely when things are going wrong.
    """
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
    # 503 rather than 500: this is "come back later", and it is what the queue's backoff is for.
    raise HTTPException(status_code=503, detail=reason[:200])
