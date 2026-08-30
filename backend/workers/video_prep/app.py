"""`worker-video-prep` service — the Cloud Tasks target for the `video_prep` stage (spec 03 §4).

Every other perception worker consumes renders. This one *produces* them, which makes it the only
B2 worker that reads the raw bucket and the only one that enqueues downstream work. Order:

    claim → load event → chaos gate → download original → ffprobe → poster → keyframes → proxy
          → upload renders → settle (video_prep done + curate/faces/safety pending) → dispatch

Four things about that order are load-bearing.

**The three downstream stage flags are written in the same transaction that completes this one.**
`shared/pipeline.py::_derive_status` flips `status='indexed'` when *every* key in the document's own
`stages` map is `done` — so a clip whose map held only `{video_prep: done}` would be declared indexed
the instant this worker finished, before the Curator or the Guardian had ever seen it, and every
public surface filters on exactly that flag. Seeding the three `pending` flags in the same write is
what makes the derived status honest. It is the same trick `intake/app.py` plays for photos, one stage
later.

**Dispatch happens after the settle, never before.** A crash between them leaves three stages
`pending` for the sweeper; enqueueing first and crashing would pay Gemini twice. Cost bugs are worse
than latency bugs — `intake/app.py` says the same thing about the same trade.

**The poster is run through `intake/images.py::render_variants`.** A clip therefore ends up with the
same `thumbUri`/`classifyUri`/`displayUri` triple a photo has, which is why the gallery grid, the
lightbox, the kiosk hero slot and both Gemini workers need no video branch at all — they were already
reading those fields. `posterUri` is stored alongside as the semantic name for the same still.

**A probe failure is permanent; a transcode failure is not.** `ffprobe` refusing the file means the
bytes are not a video we can read, and no retry changes that. ffmpeg failing mid-transcode is far
more often a resource problem — a container under memory pressure, a timeout on a long clip — so it
goes back to the queue and only quarantines when the attempts are spent.

Status codes are the contract with Cloud Tasks: 5xx means retry, 2xx means "retrying will not help".
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from google.api_core import exceptions as gexc

from intake import images
from schemas.common import MediaStatus, Stage, StageState
from shared import chaos, fs, gcs, log, pipeline, tasks
from shared.settings import MAX_VIDEO_DURATION_SEC, settings

from . import ffmpeg_ops

log.configure("worker-video-prep")

app = FastAPI(title="Showrunner Video Prep Worker", version="0.1.0", docs_url=None, redoc_url=None)

STAGE = Stage.VIDEO_PREP

#: What this worker hands to the perception queues once it has produced something for them to look at.
#: Identical to `intake.PHOTO_STAGES` on purpose: a clip and a still are the same three questions.
DOWNSTREAM_STAGES = (Stage.CURATE, Stage.FACES, Stage.SAFETY)

#: Spec 03 §6's conservative default for this stage. There is no partial answer to give — without a
#: poster the clip has no render any downstream worker can read — so the default is simply "no
#: renders", and the item stays with its uploader at `pool`, never reaching `indexed`.
CONSERVATIVE_DEFAULT: dict[str, Any] = {"keyframeUris": []}


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "worker-video-prep", "environment": settings().environment}


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
        return await _transient(request, claim, event_id, media_id, event, started, injected)

    try:
        ffmpeg_ops.require_ffmpeg()
    except RuntimeError as exc:
        # The wrong image is deployed. Transient rather than permanent: a corrected deployment should
        # let the queued task succeed on retry instead of having marked the clip unprocessable.
        return await _transient(request, claim, event_id, media_id, event, started, str(exc))

    media = claim.media
    parsed = gcs.parse_gs_uri(str(media.get("gcsUri") or ""))
    if parsed is None:
        return await _permanent(
            event_id, media_id, event, started, f"no readable gcsUri ({media.get('gcsUri')!r})"
        )

    try:
        result = await run_in_threadpool(
            _prepare, event_id, media_id, parsed[0], parsed[1], event
        )
    except ffmpeg_ops.ProbeError as exc:
        # Not a video we can read. The same bytes will fail identically forever.
        return await _permanent(event_id, media_id, event, started, f"probe failed: {exc}")
    except _Rejected as exc:
        return await _reject(event_id, media_id, event, started, str(exc), exc.reason)
    except gexc.NotFound:
        # Deleted between intake and now — a guest withdrawing an upload, not a bug.
        return await _permanent(event_id, media_id, event, started, "original object missing")
    except ffmpeg_ops.TranscodeError as exc:
        return await _transient(request, claim, event_id, media_id, event, started, f"ffmpeg: {exc}")
    except Exception as exc:  # noqa: BLE001 - GCS or memory pressure is the queue's problem
        return await _transient(request, claim, event_id, media_id, event, started, f"prep failed: {exc}")

    fields, probe = result

    # The three downstream flags ride this transaction — see the module docstring. Without them the
    # derived status would call the clip `indexed` before any perception ran.
    for stage in DOWNSTREAM_STAGES:
        fields[f"stages.{stage.value}"] = StageState.PENDING.value
        fields[f"stageTimings.{stage.value}.queuedAt"] = fs.SERVER_TIMESTAMP

    visibility = await run_in_threadpool(
        pipeline.complete_stage,
        event_id,
        media_id,
        STAGE,
        fields=fields,
        event=event,
        started=started,
        seconds=probe.duration_sec,
        keyframes=len(fields.get("keyframeUris") or []),
        codec=probe.codec,
        audio=probe.has_audio,
    )

    dispatched = await run_in_threadpool(
        _dispatch, event_id, media_id, media.get("bountyId"), bool(media.get("batchLead"))
    )
    return {
        "ok": True,
        "durationSec": probe.duration_sec,
        "keyframes": len(fields.get("keyframeUris") or []),
        "visibility": visibility,
        "dispatched": dispatched,
    }


# ---------------------------------------------------------------- the work


class _Rejected(Exception):
    """The clip is readable but out of bounds. Permanent, and recorded with a machine reason."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _prepare(
    event_id: str,
    media_id: str,
    bucket: str,
    path: str,
    event: dict[str, Any],
) -> tuple[dict[str, Any], ffmpeg_ops.Probe]:
    """Download, probe, render, upload. Returns the field updates plus the probe, for logging.

    Everything happens inside one temp directory that is removed on the way out, including on a
    failure: this service runs at concurrency 2 on a 4 GiB instance, and a 200 MB original plus a
    proxy plus twelve frames left behind by a crashed task would fill the container's disk within a
    handful of clips.
    """
    cfg = settings()
    with tempfile.TemporaryDirectory(prefix=f"vp-{media_id}-") as workdir:
        source = os.path.join(workdir, "original")
        raw = gcs.download_bytes(bucket, path)
        with open(source, "wb") as handle:
            handle.write(raw)
        del raw  # a 200 MB original does not need to stay resident through three transcodes

        probe = ffmpeg_ops.probe(source)
        if probe.duration_sec > MAX_VIDEO_DURATION_SEC:
            raise _Rejected(
                f"{probe.duration_sec:.0f}s exceeds the {MAX_VIDEO_DURATION_SEC}s cap",
                "too_long",
            )

        updates: dict[str, Any] = {
            "durationSec": probe.duration_sec,
            "hasAudio": probe.has_audio,
        }

        # ---- poster → the same three renders a photo gets, so no downstream surface needs a branch
        poster_png = ffmpeg_ops.poster_frame(source, workdir, probe.duration_sec)
        try:
            still = images.open_image(poster_png)
        except images.DecodeError as exc:
            raise ffmpeg_ops.TranscodeError(f"poster frame did not decode: {exc}") from exc
        try:
            renders, width, height = images.render_variants(still)
        finally:
            still.close()

        for render in renders:
            uri = gcs.upload_bytes(
                cfg.derived_bucket,
                gcs.derived_path(event_id, media_id, render.name),
                render.data,
                content_type=render.content_type,
                cache_control="public, max-age=31536000, immutable",
            )
            if render.name.startswith("thumb"):
                updates["thumbUri"] = uri
            elif render.name.startswith("classify"):
                updates["classifyUri"] = uri
            else:
                updates["displayUri"] = uri
                # Same object, second name. `posterUri` is what spec 03 §4 calls it and what
                # `worker-curate`/`worker-safety` fall back to; `displayUri` is what the gallery and
                # the kiosk already read. One upload, both readers satisfied.
                updates["posterUri"] = uri

        updates["width"] = width
        updates["height"] = height

        # ---- keyframes: what the Curator and the Guardian actually judge
        frames = ffmpeg_ops.keyframes(source, workdir, probe.duration_sec)
        updates["keyframeUris"] = [
            gcs.upload_bytes(
                cfg.derived_bucket,
                gcs.derived_path(event_id, media_id, f"kf_{index:02d}.webp"),
                data,
                content_type="image/webp",
                cache_control="public, max-age=31536000, immutable",
            )
            for index, data in enumerate(frames, start=1)
        ]

        # ---- proxy: in-app playback
        updates["proxyUri"] = gcs.upload_bytes(
            cfg.derived_bucket,
            gcs.derived_path(event_id, media_id, "proxy_720.mp4"),
            ffmpeg_ops.proxy(source, workdir),
            content_type="video/mp4",
            cache_control="public, max-age=31536000, immutable",
        )

        # ---- capture time. A container's `creation_time` is normally offset-aware, unlike photo
        # EXIF, so it is parsed rather than localised into the event timezone. Absent is common
        # (WhatsApp strips it), and `exifMissing` is the same flag photos use — `workers/curate/
        # fusion.py` reads it to flatten the temporal prior, which is exactly as right for a
        # forwarded clip as for a forwarded still.
        captured = _creation_time(probe.creation_time)
        updates["exifMissing"] = captured is None
        updates["capturedAt"] = captured or dt.datetime.now(dt.timezone.utc)

        return updates, probe


def _creation_time(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        log.warn("video_creation_time_unparseable", value=raw[:64])
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _dispatch(
    event_id: str, media_id: str, bounty_id: Any, batch_lead: bool
) -> list[str]:
    """Fan out to the same three perception queues a photo takes.

    The priority-lane rule is copied from `intake/app.py::_dispatch` rather than reinvented: a bounty
    submission or a batch lead expedites the classify hop, because the guest is standing there waiting
    to know whether their shot counted. A clip has already waited for a transcode, which is an argument
    for keeping the lane rather than dropping it.
    """
    cfg = settings()
    expedite = bool(bounty_id) or batch_lead
    plan = [
        (Stage.CURATE, cfg.priority_queue if expedite else cfg.classify_queue, cfg.curate_url),
        (Stage.FACES, cfg.face_queue, cfg.face_url),
        (Stage.SAFETY, cfg.safety_queue, cfg.safety_url),
    ]

    dispatched: list[str] = []
    for stage, queue, url in plan:
        try:
            tasks.enqueue(
                queue,
                url,
                {"eventId": event_id, "mediaId": media_id, "stage": stage.value},
                stage=stage.value,
                event_id=event_id,
                media_id=media_id,
            )
        except Exception as exc:  # noqa: BLE001 - one bad queue must not strand the other stages
            log.error(
                "dispatch_failed",
                event_id=event_id,
                media_id=media_id,
                stage=stage.value,
                err=str(exc),
            )
            fs.ops_alert(
                event_id,
                "dispatch_failed",
                f"could not enqueue {stage.value} after video prep: {exc}",
                media_id=media_id,
                severity="error",
            )
        else:
            dispatched.append(stage.value)
    return dispatched


# ---------------------------------------------------------------- failure paths


async def _reject(
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    reason: str,
    machine_reason: str,
) -> dict[str, Any]:
    """A readable clip that is out of bounds. `status='rejected'`, and the bytes are left in place.

    Deliberately unlike `intake._reject`, which deletes the object: intake rejects bytes it could not
    decode or that broke a declared cap, whereas this is a policy ceiling on a file the uploader
    legitimately holds. Deleting their footage because it ran four seconds long would be the system
    destroying a guest's own media over a configuration value.
    """
    await run_in_threadpool(
        fs.media_ref(event_id, media_id).set,
        {
            "status": MediaStatus.REJECTED.value,
            "rejectedReason": machine_reason,
            "rejectedAt": fs.SERVER_TIMESTAMP,
            f"stages.{STAGE.value}": StageState.FAILED_PERMANENT.value,
            f"stageErrors.{STAGE.value}": reason[:500],
        },
        merge=True,
    )
    await run_in_threadpool(
        fs.ops_alert,
        event_id,
        f"video_rejected_{machine_reason}",
        f"video rejected at prep: {reason}",
        media_id=media_id,
        severity="warning",
        stage=STAGE.value,
    )
    log.stage(
        "rejected",
        stage=STAGE.value,
        event_id=event_id,
        media_id=media_id,
        ms=pipeline.elapsed_ms(started),
        err=reason[:300],
    )
    return {"ok": True, "action": "rejected", "reason": machine_reason}


async def _permanent(
    event_id: str,
    media_id: str,
    event: dict[str, Any],
    started: dt.datetime,
    reason: str,
) -> dict[str, Any]:
    """Absorb it here: conservative default, alert, 200 so Tasks stops."""
    await run_in_threadpool(
        pipeline.fail_stage,
        event_id,
        media_id,
        STAGE,
        reason=reason,
        permanent=True,
        defaults=dict(CONSERVATIVE_DEFAULT),
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
) -> dict[str, Any]:
    """Hand back to the queue — unless this was the last attempt, which quarantines instead."""
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
