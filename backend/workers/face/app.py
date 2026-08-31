"""`worker-face` service — the Cloud Tasks target for the `faces` stage, and the one place
InsightFace is loaded (`api` calls `POST /embed` here for selfie enrollment rather than loading
the model itself — spec 09 §1's whole reason for a separate 1 GB image).

Same order as `worker-curate` (spec 03 §5.2), no LLM in the loop:

    claim → load event → chaos gate → fetch render → detect+embed → match/cluster → commit

The one structural difference from Curate: this stage's "model call" cannot fail with a 429 or a
content refusal, so there is no `PermanentModelError`/`ModelError` split to inherit. What remains
transient is exactly what would be transient for any stage — GCS hiccups, a vector index still
building, a Firestore blip; a decode failure is the only thing this stage calls permanent.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from google.api_core import exceptions as gexc

from schemas.common import MediaKind, Stage
from schemas.faces import EmbedRequest, EmbedResponse, FaceDetection
from shared import chaos, faces as faces_lib, fs, gcs, log, pipeline
from shared.settings import MAX_FACES_PER_MEDIA, SELFIE_MAX_BYTES, settings
from shared.ulid import new_ulid

from . import analyzer

log.configure("worker-face")

app = FastAPI(title="Showrunner Face Worker", version="0.1.0", docs_url=None, redoc_url=None)

STAGE = Stage.FACES

#: Spec 03 §6's conservative default: no faces indexed rather than a half-written album.
CONSERVATIVE_DEFAULT: dict[str, Any] = {"faces": [], "albumOf": []}


@app.get("/livez")
async def livez() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "worker-face",
        "environment": settings().environment,
        "modelLoadSeconds": analyzer.MODEL_LOAD_SECONDS,
    }


# ---------------------------------------------------------------- /embed (selfie path)


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    """Internal call from `api` — enrollment/re-claim selfies never reach GCS or Firestore here.

    IAM-gated to `sa-api` and `sa-tasks` at the Cloud Run layer (deploy/up.sh); this handler adds
    no auth of its own on top of that, same as every other Cloud Tasks target in this system.
    """
    try:
        image_bytes = base64.b64decode(req.image, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"selfie is not valid base64: {exc}") from exc
    if len(image_bytes) > SELFIE_MAX_BYTES:
        raise HTTPException(400, f"selfie exceeds {SELFIE_MAX_BYTES} bytes")

    try:
        detections = await run_in_threadpool(analyzer.detect, image_bytes)
    except analyzer.DecodeError as exc:
        raise HTTPException(400, f"could not decode selfie: {exc}") from exc

    top = detections[: max(1, req.maxFaces)]
    return EmbedResponse(
        faces=[
            FaceDetection(box=d.box, embedding=d.embedding, detScore=d.detScore) for d in top
        ]
    )


# ---------------------------------------------------------------- / (Cloud Tasks target)


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

    # ---- what this worker looks at, which depends on the kind:
    #
    # A **photo**: `display_1600`, falling back to `classify_768`. The big render on purpose — a face
    # at the back of a group shot is a handful of pixels at 768.
    #
    # A **video**: the keyframe grid `worker-video-prep` produced (spec 03 §4 step 3), not the poster.
    # One frame would make a clip's album membership depend on which instant the poster sampler
    # happened to like, and the person who walks into shot at second nine would never be indexed.
    media = claim.media
    is_video = media.get("kind") == MediaKind.VIDEO.value
    sources = _sources_for(media, is_video)
    if not sources:
        return await _permanent(
            event_id, media_id, event, started, f"no usable render (kind={media.get('kind')!r})"
        )

    try:
        detections = await run_in_threadpool(_detect_all, sources, is_video)
    except analyzer.DecodeError as exc:
        return await _permanent(event_id, media_id, event, started, f"decode failed: {exc}")
    except gexc.NotFound:
        return await _permanent(event_id, media_id, event, started, "render object missing")
    except Exception as exc:  # noqa: BLE001 - GCS being unhappy is the queue's problem, not ours
        return await _transient(request, claim, event_id, media_id, event, started, f"download failed: {exc}")

    try:
        result = await run_in_threadpool(
            _fuse_and_commit, event_id, media_id, detections, event, started
        )
    except Exception as exc:  # noqa: BLE001 - vector index lag / Firestore blips are transient
        return await _transient(request, claim, event_id, media_id, event, started, f"index failed: {exc}")

    return {"ok": True, **result}


# ---------------------------------------------------------------- what to look at


def _sources_for(media: dict[str, Any], is_video: bool) -> list[str]:
    """The `gs://` URIs to run detection over, in the order they should be scanned."""
    if is_video:
        return [str(u) for u in (media.get("keyframeUris") or []) if u]
    uri = media.get("displayUri") or media.get("classifyUri")
    return [str(uri)] if uri else []


def _detect_all(sources: list[str], is_video: bool) -> list[analyzer.Detection]:
    """Detect across every source, then dedupe *within this clip* (spec 03 §4 step 3).

    Two things make the video path different from running the photo path twelve times.

    **Deduping is by embedding, not by box.** A guest standing still for eight seconds appears in
    eight keyframes; without a dedupe they become eight face documents, eight vector-search round
    trips and eight album memberships for one person in one clip. Boxes cannot answer this — the
    camera pans — so the test is cosine similarity against the faces already kept, at `tau_cluster`,
    the same threshold `shared/faces.py` uses to decide two embeddings are the same person. Reused
    rather than re-derived so "the same person" means one thing in this system.

    **Largest-box-first ordering is preserved across frames.** `analyzer.detect` already sorts each
    frame that way, and the kept list is re-sorted at the end, so the `MAX_FACES_PER_MEDIA` truncation
    in `_fuse_and_commit` still cuts the people the clip is *about* last — same guarantee a photo gets.

    A frame that fails to decode is skipped rather than fatal: eleven good keyframes are a better
    answer than a permanent failure over one bad WebP.
    """
    kept: list[analyzer.Detection] = []
    for index, uri in enumerate(sources):
        parsed = gcs.parse_gs_uri(uri)
        if parsed is None:
            continue
        image_bytes = gcs.download_bytes(parsed[0], parsed[1])
        if not is_video:
            # A photo has exactly one source and no dedupe to do; let a DecodeError propagate, since
            # an undecodable single render is genuinely a permanent failure for this stage.
            return analyzer.detect(image_bytes)
        try:
            frame = analyzer.detect(image_bytes)
        except analyzer.DecodeError as exc:
            log.warn("keyframe_decode_failed", frame=index, err=str(exc))
            continue
        for detection in frame:
            if any(
                faces_lib.cosine(detection.embedding, seen.embedding) >= settings().tau_cluster
                for seen in kept
            ):
                continue
            kept.append(detection)
            if len(kept) >= MAX_FACES_PER_MEDIA:
                # Enough: the cap is a write-amplification rail, and scanning further frames only to
                # discard them is a download per frame for nothing.
                break
        if len(kept) >= MAX_FACES_PER_MEDIA:
            break

    kept.sort(key=lambda d: d.box.w * d.box.h, reverse=True)
    return kept


# ---------------------------------------------------------------- fuse + commit


def _fuse_and_commit(
    event_id: str,
    media_id: str,
    detections: list[analyzer.Detection],
    event: dict[str, Any],
    started: dt.datetime,
) -> dict[str, Any]:
    """Match against enrolled people, cluster the rest, write face docs, complete the stage.

    Truncated to `MAX_FACES_PER_MEDIA` largest-first (already the analyzer's sort order) before
    any of this runs — a 60-face baraat shot must not turn into 60 vector-search round trips.
    """
    kept = detections[:MAX_FACES_PER_MEDIA]
    cfg = settings()
    # Only people whose album the host has approved. This is the same claim-integrity gate as the one
    # in `api/identity.py`, seen from the other side, and it has to exist here too: this path matches
    # at τ_match (0.45) — looser than τ_claim by design, because a face across a dark reception hall
    # is not a selfie — with no protection check and no idea whether a claim is sitting in the review
    # queue. Without the filter a pending enrollment would quietly accrete an album while the host had
    # not yet said yes, so by the time they looked at the review card the photographs it links would
    # already be in a stranger's private album. Host-seeded people carry `claimApproved: True`
    # (`backend/seed.py`), so the standing demo event's cast is unaffected.
    enrolled = [
        person for person in faces_lib.enrolled_people(event_id) if person.get("claimApproved")
    ]

    face_refs: list[dict[str, Any]] = []
    album: set[str] = set()
    batch = fs.db().batch()

    for idx, det in enumerate(kept):
        face_id = f"{media_id}-{idx:02d}"
        person_id: str | None = None
        cluster_id: str | None = None
        match_score: float | None = None
        cluster_score: float | None = None

        hits = faces_lib.match_people(
            event_id, det.embedding, min_similarity=cfg.tau_match, people=enrolled
        )
        if hits:
            person_id, match_score = hits[0].personId, hits[0].similarity
            album.add(person_id)
        else:
            adopted = faces_lib.nearest_cluster(event_id, det.embedding, exclude_media=media_id)
            if adopted is not None:
                cluster_id, cluster_score = adopted.clusterId, adopted.similarity
            else:
                cluster_id = new_ulid()

        batch.set(
            fs.face_ref(event_id, face_id),
            {
                "faceId": face_id,
                "mediaId": media_id,
                "box": det.box.model_dump(mode="json"),
                "embedding": faces_lib.to_vector(det.embedding),
                "personId": person_id,
                "clusterId": cluster_id,
                "claimId": None,
                "detScore": det.detScore,
                "matchScore": match_score,
                "clusterScore": cluster_score,
                "createdAt": fs.SERVER_TIMESTAMP,
                "claimedAt": fs.SERVER_TIMESTAMP if person_id else None,
            },
        )
        face_refs.append(
            {
                "faceId": face_id,
                "box": det.box.model_dump(mode="json"),
                "personId": person_id,
                "clusterId": cluster_id,
            }
        )

    batch.commit()

    visibility = pipeline.complete_stage(
        event_id,
        media_id,
        STAGE,
        fields={"faces": face_refs, "albumOf": sorted(album)},
        event=event,
        started=started,
        faces=len(face_refs),
    )
    return {"faces": len(face_refs), "matched": len(album), "visibility": visibility}


# ---------------------------------------------------------------- failure paths


async def _permanent(
    event_id: str, media_id: str, event: dict[str, Any], started: dt.datetime, reason: str
) -> dict[str, Any]:
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
