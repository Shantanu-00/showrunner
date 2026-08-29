"""Upload intent registration + signed URL issuance (spec 01 §3).

Order of operations matters and is deliberate:

1. Validate the request against the allowlists and caps — cheap, no I/O.
2. Check the event's master switch (`status`, spec 08 §2) — uploads are closed in
   draft/paused/wrapped, and that is a 403, not a silent no-op.
3. In **one transaction**: enforce the per-uid rate limit and the ban flag, then create (or
   resolve) every media doc. Rate limiting and doc creation cannot disagree.
4. Only then mint signed URLs / resumable sessions. Nothing about signing touches state, so it
   sits outside the transaction where a retry is free.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Path
from google.cloud import firestore

from schemas.common import MediaKind, MediaStatus
from schemas.event import EventStatus, UPLOAD_OPEN_STATUSES
from schemas.uploads import (
    RefreshUrlResponse,
    UploadFileRequest,
    UploadsRequest,
    UploadsResponse,
    UploadTarget,
)
from shared import errors, fs, gcs, log
from shared.auth import Principal, caller
from shared.settings import (
    ALLOWED_CONTENT_TYPES,
    MAX_PHOTO_BYTES,
    MAX_VIDEO_BYTES,
    PHOTO_CONTENT_TYPES,
    settings,
)
from shared.ulid import is_ulid

from .membership import require_member_if_invite

router = APIRouter(prefix="/v1/events/{eventId}", tags=["uploads"])

#: How long after `wrapping` begins the outbox may still drain (spec 08 §2 grace period).
WRAPPING_GRACE = dt.timedelta(minutes=30)


# ---------------------------------------------------------------- validation helpers


def _kind_for(content_type: str) -> MediaKind:
    return MediaKind.PHOTO if content_type in PHOTO_CONTENT_TYPES else MediaKind.VIDEO


def _validate_file(f: UploadFileRequest) -> MediaKind:
    ct = f.contentType.split(";", 1)[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise errors.bad_request(
            "UNSUPPORTED_TYPE", f"content type not accepted: {ct}", mediaId=f.clientMediaId
        )
    kind = _kind_for(ct)
    cap = MAX_PHOTO_BYTES if kind is MediaKind.PHOTO else MAX_VIDEO_BYTES
    if f.size > cap:
        raise errors.bad_request(
            "TOO_LARGE",
            f"{kind.value} exceeds the {cap // (1024 * 1024)} MB cap",
            mediaId=f.clientMediaId,
        )
    return kind


def _require_uploads_open(event: dict[str, object]) -> None:
    raw_status = str(event.get("status") or EventStatus.DRAFT.value)
    if raw_status not in {s.value for s in UPLOAD_OPEN_STATUSES}:
        raise errors.forbidden(
            "EVENT_NOT_LIVE",
            f"uploads are closed while the event is {raw_status}",
            status=raw_status,
        )
    if raw_status == EventStatus.WRAPPING.value:
        # Grace window: in-flight outbox items are accepted, new intents are not (spec 08 §2).
        started = event.get("wrappingAt")
        if isinstance(started, dt.datetime):
            deadline = started + WRAPPING_GRACE
            if dt.datetime.now(dt.timezone.utc) > deadline:
                raise errors.forbidden(
                    "GRACE_ENDED", "the upload grace period for this event has ended"
                )


def _resolve_bounty(event_id: str, bounty_id: str | None) -> str | None:
    """A bountyId that isn't an active bounty in this event is dropped silently (spec 01 §3).

    Silent because the alternative is worse: a guest whose bounty expired mid-selection would
    get an error instead of a successful upload, and the photo is still wanted either way.
    """
    if not bounty_id:
        return None
    if not is_ulid(bounty_id):
        return None
    snap = fs.bounty_ref(event_id, bounty_id).get()
    if snap.exists and (snap.to_dict() or {}).get("status") == "active":
        return bounty_id
    log.info("bounty_dropped", event_id=event_id, bounty=bounty_id)
    return None


# ---------------------------------------------------------------- the transaction


@firestore.transactional
def _register_batch(
    transaction: firestore.Transaction,
    event_id: str,
    principal: Principal,
    req: UploadsRequest,
    kinds: dict[str, MediaKind],
    bounty_id: str | None,
) -> list[tuple[str, MediaKind, str, str]]:
    """Rate-limit, then create or resolve every media doc. Returns issuance instructions.

    Re-registering the same `clientMediaId` resolves to the existing doc rather than 409ing:
    the client outbox retries by design (spec 01 §2.1), and the object path is derived from the
    mediaId, so a re-issued URL overwrites the same object — naturally idempotent.
    """
    cfg = settings()
    guest_ref = fs.guest_ref(event_id, principal.uid)
    media_refs = [fs.media_ref(event_id, f.clientMediaId) for f in req.files]

    guest_snap = guest_ref.get(transaction=transaction)
    media_snaps = list(fs.db().get_all(media_refs, transaction=transaction))
    existing = {snap.reference.id: snap for snap in media_snaps}

    guest = guest_snap.to_dict() if guest_snap.exists else {}
    if guest.get("banned"):
        raise errors.forbidden("GUEST_BANNED", "this guest cannot upload to this event")

    now = dt.datetime.now(dt.timezone.utc)
    window_started = guest.get("rateWindowStartedAt")
    window_count = int(guest.get("rateWindowCount") or 0)
    if not isinstance(window_started, dt.datetime) or now - window_started >= dt.timedelta(hours=1):
        window_started, window_count = now, 0

    new_files = [f for f in req.files if f.clientMediaId not in existing]
    if window_count + len(new_files) > cfg.upload_rate_limit_per_hour:
        raise errors.rate_limited(
            f"upload limit of {cfg.upload_rate_limit_per_hour} files/hour reached",
            retryAfterSeconds=int(
                (window_started + dt.timedelta(hours=1) - now).total_seconds()
            ),
        )

    instructions: list[tuple[str, MediaKind, str, str]] = []
    for idx, f in enumerate(req.files):
        media_id = f.clientMediaId
        kind = kinds[media_id]
        content_type = f.contentType.split(";", 1)[0].strip().lower()
        path = gcs.original_path(event_id, media_id, content_type)
        snap = existing.get(media_id)

        if snap is not None and snap.exists:
            doc = snap.to_dict() or {}
            if doc.get("uploaderUid") != principal.uid:
                # A guest can never obtain a URL for someone else's mediaId (spec 01 §3).
                raise errors.forbidden("NOT_OWNER", "this media belongs to another uploader")
            if doc.get("status") not in (
                MediaStatus.AWAITING_UPLOAD.value,
                MediaStatus.UPLOADED.value,
            ):
                # Already processed: hand back the path, don't reopen the doc.
                instructions.append((media_id, kind, content_type, path))
                continue
            # No `batchLead` here on purpose: the doc already carries whatever the batch decided the
            # first time, and a retrying outbox must not move the fast lane to a different photo.
            transaction.update(
                media_refs[idx],
                {
                    "consent": {"ring": int(req.consent.ring)},
                    "batchId": req.batchId,
                    "bountyId": bounty_id,
                    "size": f.size,
                    "reissuedAt": fs.SERVER_TIMESTAMP,
                },
            )
        else:
            transaction.set(
                fs.media_ref(event_id, media_id),
                {
                    "mediaId": media_id,
                    "uploaderUid": principal.uid,
                    "batchId": req.batchId,
                    # The first file of each selection takes the priority classify lane at intake
                    # (spec 09 §2's `priority-queue`, EXECUTION-PLAN §7d). Under a burst, FIFO would
                    # put a guest who uploads at t=120s behind a thousand photos from t=0 — the
                    # person most likely to be watching the wall gets the worst latency. One photo
                    # per uploader jumping the queue is what makes "phone → wall in ~2 s" true
                    # during a burst rather than only on a quiet system. Chosen at intent time
                    # rather than by arrival order because it costs no read and the effect is the
                    # same: exactly one photo per batch, whichever the client sent first.
                    "batchLead": idx == 0,
                    "kind": kind.value,
                    "contentType": content_type,
                    "size": f.size,
                    "fileName": f.fileName,
                    "bountyId": bounty_id,
                    "consent": {"ring": int(req.consent.ring)},
                    "subjectVetoes": [],
                    "status": MediaStatus.AWAITING_UPLOAD.value,
                    "stages": {},
                    "attempts": {},
                    "albumOf": [],
                    "deleted": False,
                    # Client-declared capture time is a hint only; intake overwrites it from
                    # EXIF, interpreted in the event timezone (spec 03 §5.1).
                    "capturedAt": f.capturedAt,
                    "createdAt": fs.SERVER_TIMESTAMP,
                },
            )
        instructions.append((media_id, kind, content_type, path))

    transaction.set(
        guest_ref,
        {
            "uid": principal.uid,
            "personId": principal.person_id,
            "rateWindowStartedAt": window_started,
            "rateWindowCount": window_count + len(new_files),
            "uploads": firestore.Increment(len(new_files)),
            "lastSeenAt": fs.SERVER_TIMESTAMP,
            "createdAt": guest.get("createdAt") or fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return instructions


# ---------------------------------------------------------------- issuance


def _issue(
    event_id: str, media_id: str, kind: MediaKind, content_type: str, path: str, size: int
) -> UploadTarget:
    cfg = settings()
    if kind is MediaKind.PHOTO:
        url, expires = gcs.signed_put_url(
            cfg.raw_bucket, path, content_type=content_type, content_length=size
        )
        return UploadTarget(
            mediaId=media_id,
            kind=kind,
            signedUrl=url,
            objectPath=path,
            expiresAt=expires,
        )
    session_uri = gcs.resumable_session(
        cfg.raw_bucket, path, content_type=content_type, content_length=size
    )
    # The session URI is a bearer token valid for a week — deliberately never logged.
    return UploadTarget(
        mediaId=media_id,
        kind=kind,
        resumableSessionUri=session_uri,
        objectPath=path,
    )


# ---------------------------------------------------------------- routes


@router.post("/uploads", response_model=UploadsResponse)
async def create_uploads(
    req: UploadsRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> UploadsResponse:
    kinds = {f.clientMediaId: _validate_file(f) for f in req.files}
    if len({f.clientMediaId for f in req.files}) != len(req.files):
        raise errors.bad_request("DUPLICATE_IDS", "clientMediaId repeated within one batch")

    event = fs.get_event(eventId)
    if not event:
        raise errors.not_found("NO_EVENT", "unknown event")
    _require_uploads_open(event)
    # Costs no read — `event` is already in hand. Only bites on an invite-only event; see
    # `api/membership.py::require_member_if_invite` for why reads and writes differ here.
    require_member_if_invite(event, eventId, principal)

    bounty_id = _resolve_bounty(eventId, req.bountyId)
    instructions = _register_batch(
        fs.db().transaction(), eventId, principal, req, kinds, bounty_id
    )

    sizes = {f.clientMediaId: f.size for f in req.files}
    uploads = [
        _issue(eventId, media_id, kind, content_type, path, sizes[media_id])
        for media_id, kind, content_type, path in instructions
    ]
    log.info(
        "intent_registered",
        event_id=eventId,
        uid=principal.uid,
        batch=req.batchId,
        files=len(uploads),
        ring=int(req.consent.ring),
        bounty=bounty_id,
    )
    return UploadsResponse(uploads=uploads, ring=req.consent.ring, bountyId=bounty_id)


@router.post("/uploads/{mediaId}/refresh-url", response_model=RefreshUrlResponse)
async def refresh_url(
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> RefreshUrlResponse:
    """Re-issue a URL for the same object path (signed URLs expire after 15 min)."""
    if not is_ulid(mediaId):
        raise errors.bad_request("BAD_ID", "mediaId must be a ULID")

    event = fs.get_event(eventId)
    if not event:
        raise errors.not_found("NO_EVENT", "unknown event")
    _require_uploads_open(event)
    require_member_if_invite(event, eventId, principal)

    snap = fs.media_ref(eventId, mediaId).get()
    if not snap.exists:
        raise errors.not_found("NO_MEDIA", "no upload intent registered for this media")
    doc = snap.to_dict() or {}
    if doc.get("uploaderUid") != principal.uid:
        raise errors.forbidden("NOT_OWNER", "this media belongs to another uploader")
    if doc.get("status") not in (
        MediaStatus.AWAITING_UPLOAD.value,
        MediaStatus.UPLOADED.value,
    ):
        raise errors.conflict("ALREADY_PROCESSED", "this media has already been ingested")

    content_type = str(doc.get("contentType") or "")
    kind = MediaKind(doc.get("kind") or _kind_for(content_type).value)
    path = gcs.original_path(eventId, mediaId, content_type)
    target = _issue(eventId, mediaId, kind, content_type, path, int(doc.get("size") or 0))
    log.info("url_refreshed", event_id=eventId, media_id=mediaId, uid=principal.uid)
    return RefreshUrlResponse(upload=target)
