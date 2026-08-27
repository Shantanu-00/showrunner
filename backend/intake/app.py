"""`intake` service — the Eventarc target that turns uploaded bytes into a processed doc.

Order of operations is spec 01 §5, and every step is written to survive being run twice, because
Eventarc is at-least-once and *will* deliver the same finalize event again:

1. Unparseable object name → ack. Strays in the raw bucket must never retry.
2. No media doc → the bytes have no registered intent: quarantine them and alert.
3. Object over the kind's cap → delete and reject (the signed Content-Length makes this nearly
   impossible, which is exactly why an object that slips through is suspicious).
4. Status transaction guard → a duplicate delivery is absorbed here and nowhere else.
5. md5 dedupe → the loser is marked `duplicateOf` and consumes zero Gemini calls.
6. Photos: EXIF capture time (interpreted in the event's timezone), lossless GPS strip, three
   WebP renders. A decode failure is **permanent** — reject once, never retry.
7. Videos: hand off to `video-prep`, which is too heavy for this path (spec 03 §4).
8. Fan out perception tasks, then `status='processing'`.

The one ordering subtlety: the fan-out and the flip to `processing` happen in that order, so a
crash between them leaves stages `pending` (the sweeper's job) rather than double-charging
Gemini. Cost bugs are worse than latency bugs.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from google.api_core import exceptions as gexc
from google.cloud import firestore

from schemas.common import MediaKind, MediaStatus, Stage, StageState
from shared import fs, gcs, log, tasks
from shared.settings import (
    MAX_PHOTO_BYTES,
    MAX_VIDEO_BYTES,
    PHOTO_CONTENT_TYPES,
    settings,
)

from . import images

log.configure("intake")

app = FastAPI(title="Showrunner Intake", version="0.1.0", docs_url=None, redoc_url=None)

#: Stage flags intake hands to the perception workers for a photo.
PHOTO_STAGES = (Stage.CURATE, Stage.FACES, Stage.SAFETY)


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "intake", "environment": settings().environment}


@app.post("/")
async def on_object_finalized(request: Request) -> dict[str, Any]:
    """Eventarc `storage.object.v1.finalized`.

    Always 200 unless the failure is genuinely transient — a non-2xx here means Eventarc
    retries, and eventually dead-letters into the `dlq` service.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        log.warn("event_unparseable")
        return {"ok": True, "skipped": "unparseable_event"}

    # Binary content mode puts StorageObjectData at the top level; structured mode nests it.
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return await run_in_threadpool(process, data or {})


# ---------------------------------------------------------------- transactions


@firestore.transactional
def _claim(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    updates: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Take ownership of this object exactly once. Returns (outcome, doc).

    `uploaded` stays claimable on purpose: a transient failure mid-processing must be able to
    resume. What it must *not* do is let a re-delivery arrive after the fan-out — by then the doc
    is `processing` and this guard rejects it.
    """
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return "missing", {}
    doc = snap.to_dict() or {}
    if doc.get("status") not in (
        MediaStatus.AWAITING_UPLOAD.value,
        MediaStatus.UPLOADED.value,
    ):
        return "already", doc
    transaction.update(ref, updates)
    return "claimed", doc


@firestore.transactional
def _register_hash(
    transaction: firestore.Transaction,
    hash_ref: firestore.DocumentReference,
    media_id: str,
) -> str | None:
    """Claim `hashes/{md5}` for this media; returns the canonical mediaId if we lost.

    Per event, not global: the same stock photo at two different weddings is two legitimate
    uploads, and a global register would also leak the existence of one event's media to another.
    """
    snap = hash_ref.get(transaction=transaction)
    if snap.exists:
        canonical = (snap.to_dict() or {}).get("mediaId")
        if canonical and canonical != media_id:
            return str(canonical)
        return None  # our own earlier attempt — a retry, not a duplicate
    transaction.set(hash_ref, {"mediaId": media_id, "createdAt": fs.SERVER_TIMESTAMP})
    return None


# ---------------------------------------------------------------- helpers


def _md5_hex(md5_b64: str | None) -> str | None:
    """GCS reports md5 base64-encoded; the register keys on hex so paths stay URL-safe."""
    if not md5_b64:
        return None
    try:
        return base64.b64decode(md5_b64).hex()
    except (binascii.Error, ValueError):
        return None


def _captured_at(naive: dt.datetime | None, timezone: str | None) -> dt.datetime | None:
    """Interpret a naive EXIF timestamp in the event's timezone (spec 03 §5.1).

    EXIF carries no offset. Guessing UTC here is the bug that makes a 10 a.m. Haldi photo look
    like it was shot at 4:30 a.m. and fall outside every stage window.
    """
    if naive is None:
        return None
    try:
        tz = ZoneInfo(timezone) if timezone else dt.timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        log.warn("bad_event_timezone", timezone=timezone)
        tz = dt.timezone.utc
    return naive.replace(tzinfo=tz).astimezone(dt.timezone.utc)


def _quarantine(event_id: str, media_id: str, bucket: str, path: str) -> dict[str, Any]:
    """Bytes with no registered intent: move them out of the pipeline, keep them for forensics."""
    cfg = settings()
    name = path.rsplit("/", 1)[-1]
    try:
        gcs.copy_object(bucket, path, cfg.derived_bucket, gcs.quarantine_path(event_id, media_id, name))
    except Exception as exc:  # noqa: BLE001 - failing to preserve it must not block the delete
        log.error("quarantine_copy_failed", event_id=event_id, media_id=media_id, err=str(exc))
    gcs.delete_object(bucket, path)
    fs.ops_alert(
        event_id,
        "orphan_object",
        "an object was uploaded with no registered upload intent and has been quarantined",
        media_id=media_id,
        severity="warning",
        objectPath=path,
    )
    return {"ok": True, "action": "quarantined"}


def _reject(event_id: str, media_id: str, bucket: str, path: str, reason: str) -> dict[str, Any]:
    """Permanent rejection: the doc records why, the bytes go away, Eventarc gets a 200."""
    fs.media_ref(event_id, media_id).set(
        {
            "status": MediaStatus.REJECTED.value,
            "rejectedReason": reason,
            "rejectedAt": fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    gcs.delete_object(bucket, path)
    fs.ops_alert(
        event_id,
        f"media_rejected_{reason}",
        f"media rejected at intake: {reason}",
        media_id=media_id,
        severity="warning",
    )
    log.warn("media_rejected", event_id=event_id, media_id=media_id, reason=reason)
    return {"ok": True, "action": "rejected", "reason": reason}


def _dispatch(event_id: str, media_id: str, kind: MediaKind, bounty_id: str | None) -> list[str]:
    """Fan out to the perception queues. Videos go through `video-prep`, which then fans out.

    A bounty upload's classify hop jumps to the priority queue: the guest is standing there
    waiting to know whether their shot counted (spec 05 §3).
    """
    cfg = settings()
    payload = {"eventId": event_id, "mediaId": media_id}
    dispatched: list[str] = []

    if kind is MediaKind.VIDEO:
        plan = [(Stage.VIDEO_PREP, cfg.video_prep_queue, cfg.video_prep_url)]
    else:
        classify_queue = cfg.priority_queue if bounty_id else cfg.classify_queue
        plan = [
            (Stage.CURATE, classify_queue, cfg.curate_url),
            (Stage.FACES, cfg.face_queue, cfg.face_url),
            (Stage.SAFETY, cfg.safety_queue, cfg.safety_url),
        ]

    for stage, queue, url in plan:
        try:
            tasks.enqueue(
                queue,
                url,
                {**payload, "stage": stage.value},
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
                f"could not enqueue {stage.value}: {exc}",
                media_id=media_id,
                severity="error",
            )
        else:
            dispatched.append(stage.value)
    return dispatched


# ---------------------------------------------------------------- the handler


def process(data: dict[str, Any]) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    cfg = settings()
    bucket = str(data.get("bucket") or "")
    path = str(data.get("name") or "")
    if not bucket or not path:
        log.warn("event_missing_object", bucket=bucket, path=path)
        return {"ok": True, "skipped": "no_object"}

    parsed = gcs.parse_object_path(path)
    if not parsed:
        # Derived renders live in a different bucket, so this really is a stray.
        log.warn("stray_object", bucket=bucket, path=path)
        return {"ok": True, "skipped": "stray"}
    event_id, media_id = parsed

    size = int(data.get("size") or 0)
    generation = int(data.get("generation") or 0)
    md5_hex = _md5_hex(data.get("md5Hash"))
    object_content_type = str(data.get("contentType") or "")

    snap = fs.media_ref(event_id, media_id).get()
    if not snap.exists:
        return _quarantine(event_id, media_id, bucket, path)
    doc = snap.to_dict() or {}

    content_type = str(doc.get("contentType") or object_content_type).lower()
    kind = MediaKind.PHOTO if content_type in PHOTO_CONTENT_TYPES else MediaKind.VIDEO
    cap = MAX_PHOTO_BYTES if kind is MediaKind.PHOTO else MAX_VIDEO_BYTES
    if size > cap:
        return _reject(event_id, media_id, bucket, path, "oversize")

    outcome, doc = _claim(
        fs.db().transaction(),
        fs.media_ref(event_id, media_id),
        {
            "status": MediaStatus.UPLOADED.value,
            "kind": kind.value,
            "gcsUri": gcs.gs_uri(bucket, path),
            "objectPath": path,
            "objectGeneration": generation,
            "md5Hash": md5_hex,
            "size": size,
            "uploadedAt": fs.SERVER_TIMESTAMP,
        },
    )
    if outcome == "missing":  # deleted between the read and the transaction
        return _quarantine(event_id, media_id, bucket, path)
    if outcome == "already":
        log.info(
            "duplicate_delivery_absorbed",
            event_id=event_id,
            media_id=media_id,
            status=doc.get("status"),
            generation=generation,
        )
        return {"ok": True, "skipped": "already_processed"}

    log.info(
        "claimed",
        event_id=event_id,
        media_id=media_id,
        kind=kind.value,
        bytes=size,
        generation=generation,
    )

    # ---- dedupe (before any paid work, which is the whole point)
    canonical: str | None = None
    if md5_hex:
        canonical = _register_hash(fs.db().transaction(), fs.hash_ref(event_id, md5_hex), media_id)

    event = fs.get_event(event_id) or {}
    updates: dict[str, Any] = {}

    # ---- photos: EXIF, GPS, renders
    if kind is MediaKind.PHOTO:
        try:
            raw = gcs.download_bytes(bucket, path)
        except gexc.NotFound:
            log.warn("object_vanished", event_id=event_id, media_id=media_id, path=path)
            return {"ok": True, "skipped": "object_gone"}

        try:
            img = images.open_image(raw)
        except images.DecodeError as exc:
            # Permanent by definition: the same bytes will fail the same way forever.
            log.warn("decode_failed", event_id=event_id, media_id=media_id, err=str(exc))
            return _reject(event_id, media_id, bucket, path, "decode_failed")

        exif_at = images.read_capture_time(raw)
        captured = _captured_at(exif_at, event.get("timezone"))
        updates["exifMissing"] = captured is None
        updates["capturedAt"] = captured or dt.datetime.now(dt.timezone.utc)

        try:
            renders, width, height = images.render_variants(img)
        except Exception as exc:  # noqa: BLE001 - a decodable image that will not resize is poison too
            log.warn("render_failed", event_id=event_id, media_id=media_id, err=str(exc))
            return _reject(event_id, media_id, bucket, path, "render_failed")
        finally:
            img.close()

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

        updates["width"] = width
        updates["height"] = height
        updates[f"stages.{Stage.THUMB.value}"] = StageState.DONE.value
        updates[f"stageTimings.{Stage.THUMB.value}.doneAt"] = fs.SERVER_TIMESTAMP

        # GPS last, and only for JPEG: piexif rewrites the metadata block in place, so the
        # original stays byte-identical in pixels. The re-upload fires a second finalize event
        # which the §4 guard absorbs (the doc is `processing` by then).
        stripped = images.strip_gps(raw, content_type)
        if stripped is not None:
            try:
                gcs.upload_bytes(
                    bucket,
                    path,
                    stripped,
                    content_type=content_type,
                    if_generation_match=generation,
                )
            except gexc.PreconditionFailed:
                # Someone re-PUT the object; their finalize event will strip it instead.
                log.info("gps_strip_raced", event_id=event_id, media_id=media_id)
            else:
                updates["gpsStripped"] = True
                log.info("gps_stripped", event_id=event_id, media_id=media_id)

    if canonical:
        # Byte-identical to media already in this event. It keeps its own renders (so the
        # uploader's own album is complete) but buys no perception: spec 04 gives a dupe
        # `visibility='self'`, and B2 copies the canonical's results when they land.
        updates["duplicateOf"] = canonical
        updates["status"] = MediaStatus.INDEXED.value
        fs.media_ref(event_id, media_id).update(updates)
        log.info("duplicate_of", event_id=event_id, media_id=media_id, canonical=canonical)
        return {"ok": True, "action": "duplicate", "duplicateOf": canonical}

    bounty_id = doc.get("bountyId")
    stages = (
        [Stage.VIDEO_PREP] if kind is MediaKind.VIDEO else list(PHOTO_STAGES)
    )
    for stage in stages:
        updates[f"stages.{stage.value}"] = StageState.PENDING.value
        updates[f"stageTimings.{stage.value}.queuedAt"] = fs.SERVER_TIMESTAMP
    updates["status"] = MediaStatus.PROCESSING.value
    fs.media_ref(event_id, media_id).update(updates)

    # After the flip, never before: a crash here leaves stages `pending` for the sweeper to
    # requeue, whereas enqueueing first and crashing would pay Gemini twice.
    dispatched = _dispatch(event_id, media_id, kind, bounty_id if isinstance(bounty_id, str) else None)

    log.info(
        "intake_done",
        event_id=event_id,
        media_id=media_id,
        kind=kind.value,
        stages=",".join(dispatched),
        ms=int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000),
    )
    return {"ok": True, "action": "processing", "dispatched": dispatched}
