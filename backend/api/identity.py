"""Enrollment, claims and consent (spec 02) — the API-side half of the Face Indexer contract.

Spec 03 §5.2 is explicit that the claim-integrity gate "lives in the API enrollment path, not
[the face] worker" — this module is that path. Two routers because spec 02 §7 has exactly one
endpoint that is not event-scoped (`POST /v1/claim` — a magic-link code carries its own event):

- `router`      — `/v1/events/{eventId}/...`, everything else.
- `claim_router`— `/v1/claim`, standalone.

**Identity-granting mechanism, chosen once and used everywhere below:** `firebase_admin.auth.
set_custom_user_claims(uid, {"personId": ...})` on the *caller's own* uid, exactly as spec 02 §1
describes ("the server sets custom claims... and the client force-refreshes its ID token").
Every path here — enrollment, re-claim, magic-link redemption — grants identity to whichever uid
is already on the caller's bearer token; anonymous auth has always already run by the time any of
these are called (spec 01), so there is never a session with no uid to attach claims to, and a
second `createCustomToken` round trip buys nothing extra.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Path
from google.cloud import firestore

from schemas.common import BoundingBox, ConsentRing
from schemas.faces import FaceDetection
from schemas.identity import (
    ClaimAudit,
    ClaimExemplar,
    ClaimHoldReason,
    ClaimLinkResponse,
    ClaimMethod,
    ClaimReviewRequest,
    ClaimReviewResponse,
    ClaimStatus,
    ConsentUpdateRequest,
    EnrollOutcome,
    EnrollRequest,
    EnrollResponse,
    ReclaimRequest,
    RedeemRequest,
    RedeemResponse,
    SubjectVetoRequest,
    VisibilityResponse,
)
from schemas.person import Tier
from shared import errors, faces as faces_lib, fs, gcs, internal, log
from shared.auth import Principal, caller, merge_custom_claims
from shared.settings import (
    CLAIM_EXEMPLARS,
    CLAIM_FACE_LIMIT,
    CLAIM_LINK_TTL_DAYS,
    SELFIE_MAX_BYTES,
    settings,
)
from shared.ulid import new_ulid
from shared.visibility import recompute_visibility

router = APIRouter(prefix="/v1/events/{eventId}", tags=["identity"])
claim_router = APIRouter(prefix="/v1", tags=["identity"])


# ---------------------------------------------------------------- shared helpers


def _decode_selfie(selfie_b64: str) -> bytes:
    try:
        image_bytes = base64.b64decode(selfie_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise errors.bad_request("BAD_SELFIE", f"selfie is not valid base64: {exc}") from exc
    if not image_bytes:
        raise errors.bad_request("BAD_SELFIE", "empty selfie")
    if len(image_bytes) > SELFIE_MAX_BYTES:
        raise errors.bad_request("SELFIE_TOO_LARGE", f"selfie exceeds {SELFIE_MAX_BYTES} bytes")
    return image_bytes


def _embed_one(selfie_b64: str) -> tuple[list[float], BoundingBox, float]:
    """One face from a selfie, or a 400 — enrollment/re-claim need exactly one clear face."""
    try:
        body = internal.embed_selfie(selfie_b64, max_faces=1)
    except internal.FaceServiceError as exc:
        raise errors.ApiError(503, "FACE_SERVICE_UNAVAILABLE", str(exc)) from exc
    faces = [FaceDetection(**f) for f in body.get("faces") or []]
    if not faces:
        raise errors.bad_request("NO_FACE_DETECTED", "no face detected in the selfie")
    top = faces[0]
    return top.embedding, top.box, top.detScore


def _require_host(principal: Principal, event_id: str) -> None:
    if not (principal.is_host_of(event_id) or principal.platform_admin):
        raise errors.forbidden("HOST_ONLY", "this action requires the host")


def _require_person(principal: Principal) -> str:
    if not principal.person_id:
        raise errors.forbidden("NOT_ENROLLED", "this action requires an enrolled person")
    return principal.person_id


def _grant_identity(event_id: str, uid: str, person_id: str) -> None:
    """The one identity-granting call in this module — see the module docstring."""
    merge_custom_claims(uid, personId=person_id)
    fs.person_ref(event_id, person_id).update({"uidLinks": firestore.ArrayUnion([uid])})


def _write_audit(event_id: str, audit: ClaimAudit) -> None:
    payload = audit.model_dump(mode="json", exclude={"at", "reviewedAt"})
    payload["at"] = fs.SERVER_TIMESTAMP
    fs.claim_audit_ref(event_id, audit.claimId).set(payload)
    log.info(
        "claim_audit",
        event_id=event_id,
        claim=audit.claimId,
        status=audit.status.value,
        method=audit.method.value,
        faces=audit.faceCount,
    )


def _new_device_notice(event_id: str, person_id: str) -> None:
    fs.notices_col(event_id, person_id).add(
        {
            "kind": "new_device",
            "message": "new device joined — not you? tap here",
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )


def _build_exemplars(event_id: str, hits: list[faces_lib.FaceHit]) -> list[ClaimExemplar]:
    """Up to CLAIM_EXEMPLARS distinct-photo tiles for the host's five-second visual check."""
    seen: set[str] = set()
    exemplars: list[ClaimExemplar] = []
    for hit in sorted(hits, key=lambda h: h.similarity, reverse=True):
        if hit.mediaId in seen:
            continue
        seen.add(hit.mediaId)
        doc = fs.media_ref(event_id, hit.mediaId).get().to_dict() or {}
        exemplars.append(
            ClaimExemplar(
                mediaId=hit.mediaId,
                faceId=hit.faceId,
                box=BoundingBox(**hit.box) if hit.box else None,
                similarity=round(hit.similarity, 4),
                thumbUri=doc.get("thumbUri"),
            )
        )
        if len(exemplars) >= CLAIM_EXEMPLARS:
            break
    return exemplars


def _store_review_selfie(event_id: str, claim_id: str, image_bytes: bytes) -> str:
    return gcs.upload_bytes(
        settings().raw_bucket,
        f"events/{event_id}/claimAudits/{claim_id}/selfie.jpg",
        image_bytes,
        content_type="image/jpeg",
    )


# ---------------------------------------------------------------- enrollment (spec 02 §3)


@router.post("/people", response_model=EnrollResponse)
async def enroll(
    req: EnrollRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> EnrollResponse:
    if not req.biometricConsent:
        raise errors.bad_request("CONSENT_REQUIRED", "biometric consent is required to enroll")

    image_bytes = _decode_selfie(req.selfie)
    embedding, _box, _det_score = _embed_one(req.selfie)
    cfg = settings()

    # Impersonation guard (spec 02 §3): does this selfie already belong to someone?
    enrolled = faces_lib.enrolled_people(eventId)
    hits = faces_lib.match_people(eventId, embedding, min_similarity=cfg.tau_claim, people=enrolled)

    if hits:
        top = hits[0]
        ambiguous = faces_lib.is_ambiguous(hits)
        if top.protected or ambiguous:
            return _hold_identity_match(eventId, principal, top, ambiguous, image_bytes)
        return _apply_reclaim(eventId, principal, top, method=ClaimMethod.ENROLL)

    return _create_person_and_claim(eventId, principal, req, embedding, image_bytes)


def _create_person_and_claim(
    event_id: str,
    principal: Principal,
    req: EnrollRequest,
    embedding: list[float],
    image_bytes: bytes,
) -> EnrollResponse:
    cfg = settings()
    person_id = new_ulid()
    # The face template goes to `enrollments/{personId}`, never onto the person document: the person
    # document is readable by other guests (kiosk credits, leaderboard names, the tier→vipWeight
    # lookup) and Firestore rules cannot withhold one field of a document they grant (see
    # `fs.enrollments_col`). One extra write; the biometric never leaves the server.
    fs.enrollment_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "embedding": embedding,
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )
    fs.person_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "displayName": req.displayName,
            "uidLinks": [principal.uid],
            "tier": int(Tier.GUEST),
            "hostEnrolled": False,
            "featured": False,
            "consent": {
                "selfieEnrolled": True,
                "enrolledAt": fs.SERVER_TIMESTAMP,
                "retentionNoticeShown": req.retentionNoticeShown,
            },
            "tasteProfile": {},
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )

    hits = faces_lib.nearest_faces(
        event_id, embedding, limit=CLAIM_FACE_LIMIT, min_similarity=cfg.tau_claim
    )
    grouped = faces_lib.group_hits(hits)
    face_count = sum(len(v) for v in grouped.values())
    top_similarity = max((h.similarity for h in hits), default=0.0)
    claim_id = new_ulid()

    merge_custom_claims(principal.uid, personId=person_id)

    if face_count >= cfg.claim_review_threshold:
        selfie_uri = _store_review_selfie(event_id, claim_id, image_bytes)
        audit = ClaimAudit(
            claimId=claim_id,
            personId=person_id,
            uid=principal.uid,
            faceCount=face_count,
            topSimilarity=round(top_similarity, 4),
            method=ClaimMethod.ENROLL,
            status=ClaimStatus.HELD,
            holdReason=ClaimHoldReason.CLAIM_SIZE,
            targetPersonId=person_id,
            displayName=req.displayName,
            faceIds=grouped,
            exemplars=_build_exemplars(event_id, hits),
            selfieUri=selfie_uri,
        )
        _write_audit(event_id, audit)
        return EnrollResponse(
            outcome=EnrollOutcome.HELD_FOR_REVIEW,
            personId=person_id,
            displayName=req.displayName,
            claimId=claim_id,
            claimedFaces=0,
            topSimilarity=top_similarity,
            message=(
                "the host is confirming it's you — your own uploads are already in your album."
            ),
        )

    linked = faces_lib.link_faces(event_id, person_id, grouped, claim_id) if grouped else 0
    _write_audit(
        event_id,
        ClaimAudit(
            claimId=claim_id,
            personId=person_id,
            uid=principal.uid,
            faceCount=linked,
            topSimilarity=round(top_similarity, 4),
            method=ClaimMethod.ENROLL,
            status=ClaimStatus.APPLIED,
            targetPersonId=person_id,
            displayName=req.displayName,
        ),
    )
    return EnrollResponse(
        outcome=EnrollOutcome.LINKED,
        personId=person_id,
        displayName=req.displayName,
        claimId=claim_id,
        claimedFaces=linked,
        topSimilarity=top_similarity,
        message="enrolled — your album is ready.",
    )


def _apply_reclaim(
    event_id: str, principal: Principal, top: faces_lib.PersonHit, *, method: ClaimMethod
) -> EnrollResponse:
    """Matched an already-enrolled, non-protected person — link this uid, no gate (spec 02 §3)."""
    person_id = top.personId
    _grant_identity(event_id, principal.uid, person_id)
    _new_device_notice(event_id, person_id)
    claim_id = new_ulid()
    _write_audit(
        event_id,
        ClaimAudit(
            claimId=claim_id,
            personId=person_id,
            uid=principal.uid,
            faceCount=0,
            topSimilarity=round(top.similarity, 4),
            method=method,
            status=ClaimStatus.APPLIED,
            targetPersonId=person_id,
            displayName=top.person.get("displayName"),
        ),
    )
    return EnrollResponse(
        outcome=EnrollOutcome.LINKED,
        personId=person_id,
        displayName=top.person.get("displayName"),
        claimId=claim_id,
        claimedFaces=0,
        topSimilarity=top.similarity,
        message="welcome back — this looks like your existing album, linking you to it.",
    )


def _hold_identity_match(
    event_id: str,
    principal: Principal,
    top: faces_lib.PersonHit,
    ambiguous: bool,
    image_bytes: bytes,
) -> EnrollResponse:
    """A VIP/host-enrolled match, or a top-2 too close to call — never silently granted."""
    claim_id = new_ulid()
    selfie_uri = _store_review_selfie(event_id, claim_id, image_bytes)
    _write_audit(
        event_id,
        ClaimAudit(
            claimId=claim_id,
            personId=None,
            uid=principal.uid,
            faceCount=0,
            topSimilarity=round(top.similarity, 4),
            method=ClaimMethod.ENROLL,
            status=ClaimStatus.HELD,
            # Protected always wins even when the top-2 are also within the ambiguity margin —
            # it is the more specific, more actionable signal for the host's review card, and a
            # VIP match must never quietly read as a generic "too close to call".
            holdReason=ClaimHoldReason.PROTECTED_PERSON if top.protected else ClaimHoldReason.AMBIGUOUS_MATCH,
            targetPersonId=top.personId,
            displayName=top.person.get("displayName"),
            selfieUri=selfie_uri,
        ),
    )
    return EnrollResponse(
        outcome=EnrollOutcome.PENDING_HOST_APPROVAL,
        personId=None,
        claimId=claim_id,
        topSimilarity=top.similarity,
        message="the host needs to confirm this one — you'll get access once they do.",
    )


# ---------------------------------------------------------------- re-claim (spec 02 §3.2)


@router.post("/people/reclaim", response_model=EnrollResponse)
async def reclaim(
    req: ReclaimRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> EnrollResponse:
    embedding, _box, _det = _embed_one(req.selfie)
    cfg = settings()
    hits = faces_lib.match_people(
        eventId, embedding, min_similarity=cfg.tau_claim, people=faces_lib.enrolled_people(eventId)
    )
    if not hits:
        raise errors.forbidden("NO_MATCH", "this selfie does not match an enrolled album")
    top = hits[0]
    if faces_lib.is_ambiguous(hits):
        raise errors.forbidden(
            "AMBIGUOUS_MATCH",
            "too close to call — use your magic link, or ask the host to confirm",
        )
    if top.protected:
        image_bytes = _decode_selfie(req.selfie)
        return _hold_identity_match(eventId, principal, top, False, image_bytes)
    return _apply_reclaim(eventId, principal, top, method=ClaimMethod.RECLAIM)


# ---------------------------------------------------------------- host review (spec 02 §3.1)


@router.post("/claims/{claimId}/review", response_model=ClaimReviewResponse)
async def review_claim(
    req: ClaimReviewRequest,
    eventId: str = Path(min_length=1, max_length=128),
    claimId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> ClaimReviewResponse:
    _require_host(principal, eventId)
    ref = fs.claim_audit_ref(eventId, claimId)
    snap = ref.get()
    if not snap.exists:
        raise errors.not_found("NO_CLAIM", "unknown claim")
    audit = snap.to_dict() or {}
    if audit.get("status") != ClaimStatus.HELD.value:
        raise errors.conflict("ALREADY_REVIEWED", f"claim is already {audit.get('status')}")

    if req.decision == "deny":
        ref.update(
            {
                "status": ClaimStatus.DENIED.value,
                "reviewedBy": principal.uid,
                "reviewedAt": fs.SERVER_TIMESTAMP,
            }
        )
        log.info("claim_denied", event_id=eventId, claim=claimId, host=principal.uid)
        return ClaimReviewResponse(
            claimId=claimId, status=ClaimStatus.DENIED, personId=audit.get("personId"), linkedFaces=0
        )

    hold_reason = audit.get("holdReason")
    if hold_reason == ClaimHoldReason.CLAIM_SIZE.value:
        person_id = str(audit.get("personId") or "")
        linked = faces_lib.link_faces(eventId, person_id, audit.get("faceIds") or {}, claimId)
    else:
        person_id = str(audit.get("targetPersonId") or "")
        uid = str(audit.get("uid") or "")
        _grant_identity(eventId, uid, person_id)
        _new_device_notice(eventId, person_id)
        linked = 0

    ref.update(
        {
            "status": ClaimStatus.APPROVED.value,
            "personId": person_id,
            "reviewedBy": principal.uid,
            "reviewedAt": fs.SERVER_TIMESTAMP,
        }
    )
    log.info("claim_approved", event_id=eventId, claim=claimId, person=person_id, faces=linked)
    return ClaimReviewResponse(
        claimId=claimId, status=ClaimStatus.APPROVED, personId=person_id, linkedFaces=linked
    )


# ---------------------------------------------------------------- magic links (spec 02 §3.1)


@router.post("/claim-links", response_model=ClaimLinkResponse)
async def create_claim_link(
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> ClaimLinkResponse:
    code = secrets.token_urlsafe(16)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=CLAIM_LINK_TTL_DAYS)
    fs.claim_link_ref(code_hash).set(
        {
            "eventId": eventId,
            "personId": principal.person_id,
            "uid": principal.uid,
            "expiresAt": expires_at,
            "revoked": False,
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )
    origin = settings().app_origin or "http://localhost:3000"
    url = f"{origin.rstrip('/')}/events/{eventId}/claim#{code}"
    return ClaimLinkResponse(url=url, code=code, expiresAt=expires_at)


@claim_router.post("/claim", response_model=RedeemResponse)
async def redeem_claim_link(
    req: RedeemRequest, principal: Principal = Depends(caller)
) -> RedeemResponse:
    code_hash = hashlib.sha256(req.code.encode("utf-8")).hexdigest()
    snap = fs.claim_link_ref(code_hash).get()
    if not snap.exists:
        raise errors.forbidden("BAD_CODE", "this link is invalid")
    link = snap.to_dict() or {}
    expires_at = link.get("expiresAt")
    if link.get("revoked") or not isinstance(expires_at, dt.datetime) or (
        dt.datetime.now(dt.timezone.utc) > expires_at
    ):
        raise errors.forbidden("EXPIRED_CODE", "this link has expired or was revoked")

    event_id = str(link.get("eventId") or "")
    person_id = link.get("personId")
    if person_id:
        _grant_identity(event_id, principal.uid, str(person_id))
    display_name = None
    if person_id:
        display_name = (fs.person_ref(event_id, str(person_id)).get().to_dict() or {}).get(
            "displayName"
        )
    log.info("claim_link_redeemed", event_id=event_id, person=person_id, uid=principal.uid)
    return RedeemResponse(eventId=event_id, personId=person_id, customToken=None, displayName=display_name)


# ---------------------------------------------------------------- consent + subject veto (spec 02 §4)


@router.post("/media/{mediaId}/consent", response_model=VisibilityResponse)
async def update_consent(
    req: ConsentUpdateRequest,
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> VisibilityResponse:
    snap = fs.media_ref(eventId, mediaId).get()
    if not snap.exists:
        raise errors.not_found("NO_MEDIA", "unknown media")
    doc = snap.to_dict() or {}
    if doc.get("uploaderUid") != principal.uid:
        raise errors.forbidden("NOT_OWNER", "only the uploader may change this photo's consent")

    visibility = recompute_visibility(eventId, mediaId, extra={"consent.ring": int(req.ring)})
    log.info("consent_updated", event_id=eventId, media_id=mediaId, ring=int(req.ring))
    return VisibilityResponse(mediaId=mediaId, visibility=visibility)


@router.post("/media/{mediaId}/subject-veto", response_model=VisibilityResponse)
async def subject_veto(
    req: SubjectVetoRequest,
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> VisibilityResponse:
    person_id = _require_person(principal)
    snap = fs.media_ref(eventId, mediaId).get()
    if not snap.exists:
        raise errors.not_found("NO_MEDIA", "unknown media")
    doc = snap.to_dict() or {}
    if person_id not in (doc.get("albumOf") or []):
        raise errors.forbidden("NOT_A_SUBJECT", "you do not appear in this photo")

    op = firestore.ArrayUnion([person_id]) if req.hide else firestore.ArrayRemove([person_id])
    visibility = recompute_visibility(eventId, mediaId, extra={"subjectVetoes": op})
    log.info("subject_veto", event_id=eventId, media_id=mediaId, person=person_id, hide=req.hide)
    return VisibilityResponse(mediaId=mediaId, visibility=visibility)


# ---------------------------------------------------------------- deletion (spec 02 §5)


@router.delete("/people/me")
async def delete_me(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> dict[str, Any]:
    person_id = _require_person(principal)
    person_ref = fs.person_ref(eventId, person_id)
    person = person_ref.get().to_dict() or {}

    # Their face, wherever it appears (their own uploads too) — deleted, not just unclaimed
    # (spec 02 §5: "their face in others' photos: face doc deleted -> drops out of albums").
    grouped = faces_lib.faces_of_person(eventId, person_id)
    for media_id, face_ids in sorted(grouped.items()):
        batch = fs.db().batch()
        for face_id in face_ids:
            batch.delete(fs.face_ref(eventId, face_id))
        batch.commit()
        media_ref = fs.media_ref(eventId, media_id)
        media = media_ref.get().to_dict() or {}
        remaining = [f for f in (media.get("faces") or []) if f.get("faceId") not in set(face_ids)]
        album = sorted({str(f["personId"]) for f in remaining if f.get("personId")})
        media_ref.update({"faces": remaining, "albumOf": album})

    # Tombstone every media item any of their uids uploaded (spec 01 §5's tombstone shape).
    # Firestore's `in` filter caps at 30 values, hence the batching.
    uid_links = [u for u in (person.get("uidLinks") or []) if u]
    for start in range(0, len(uid_links), 30):
        uid_batch = uid_links[start : start + 30]
        query = fs.media_col(eventId).where(
            filter=firestore.FieldFilter("uploaderUid", "in", uid_batch)
        )
        for doc_snap in query.stream():
            recompute_visibility(eventId, doc_snap.id, extra={"deleted": True})

    for uid in uid_links:
        try:
            # Clears only `personId` — a uid that also hosts some event keeps that claim.
            merge_custom_claims(uid, personId=None)
        except Exception as exc:  # noqa: BLE001 - claim cleanup must not block the deletion
            log.warn("claim_clear_failed", uid=uid, err=str(exc))

    # The face template is a separate document (spec 02 §5's "deletes person doc + embeddings"), so
    # deleting the person is not enough — this is the write that makes the promise true.
    fs.enrollment_ref(eventId, person_id).delete()
    person_ref.delete()
    log.info("person_deleted", event_id=eventId, person=person_id, faces=sum(len(v) for v in grouped.values()))
    return {"ok": True, "personId": person_id}
