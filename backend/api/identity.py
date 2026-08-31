"""Enrollment, claims and consent (spec 02) — the API-side half of the Face Indexer contract.

Spec 03 §5.2 is explicit that the claim-integrity gate "lives in the API enrollment path, not
[the face] worker" — this module is that path. Two routers because spec 02 §7 has exactly one
endpoint that is not event-scoped (`POST /v1/claim` — a magic-link code carries its own event):

- `router`      — `/v1/events/{eventId}/...`, everything else.
- `claim_router`— `/v1/claim`, standalone.

**Identity-granting mechanism, chosen once and used everywhere below:** `firebase_admin.auth.
set_custom_user_claims(uid, {"personId": ...})` on the *caller's own* uid, exactly as spec 02 §1
describes ("the server sets custom claims... and the client force-refreshes its ID token").
Identity is granted to whichever uid is already on the caller's bearer token; anonymous auth has
always already run by the time any of these are called (spec 01), so there is never a session with
no uid to attach claims to, and a second `createCustomToken` round trip buys nothing extra.

**The host approves every album.** Two paths grant a `personId`: host approval of a claim
(`review_claim`) and redemption of a magic link the host or the person themselves issued
(`redeem_claim_link`). A *face match* grants nothing, ever. Spec 02 §3 originally split matches by
how valuable the album looked — protected (VIP / host-enrolled) matches went to the host, an
ordinary match was "treated as a re-claim of that person" and applied straight away — and that
split is precisely as strong as the assumption that only VIP albums are worth stealing. It is not:
an anonymous visitor downloaded a tier-3 guest's face off the public kiosk, submitted it as an
enrollment selfie, matched, and was handed that guest's private album, their subject-veto rights
and their delete-my-data button (which tombstones everything any linked uid ever uploaded). Making
the automatic grant narrower would have patched that instance; removing it deletes the class. So
every enrollment and every re-claim now writes a *held* claim, and a held claim grants nothing —
no custom claim, no `uidLinks` entry, no face link. What the guest keeps in the meantime is their
own uploads, which reach them through `firestore.rules`'s `uploaderUid` clause and never needed a
personId in the first place. The cost is one host tap per guest; the review queue
(`GET …/claims`) is where those taps live.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse
from google.cloud import firestore

from schemas.common import BoundingBox, ConsentRing
from schemas.faces import FaceDetection
from schemas.identity import (
    ClaimAudit,
    ClaimExemplar,
    ClaimHoldReason,
    ClaimLinkResponse,
    ClaimListResponse,
    ClaimMethod,
    ClaimReviewCard,
    ClaimReviewExemplar,
    ClaimReviewRequest,
    ClaimReviewResponse,
    ClaimStatus,
    ConsentUpdateRequest,
    EnrollOutcome,
    EnrollRequest,
    EnrollResponse,
    HostEnrollRequest,
    HostEnrollResponse,
    ReclaimRequest,
    TierRequest,
    RedeemRequest,
    RedeemResponse,
    SubjectVetoRequest,
    VisibilityResponse,
)
from schemas.person import Tier
from shared import errors, faces as faces_lib, fs, gcs, internal, log, push
from shared.auth import Principal, caller, custom_claims, merge_custom_claims
from shared.settings import (
    CLAIM_EXEMPLARS,
    CLAIM_FACE_LIMIT,
    CLAIM_LINK_TTL_DAYS,
    CLAIM_LIST_LIMIT,
    CLAIM_RATE_LIMIT_PER_HOUR,
    CLAIM_REVIEW_URL_TTL_MINUTES,
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


def _shrink_for_embed(selfie_b64: str) -> str:
    """Downscale a selfie before it rides to `worker-face` — the HANDOFF §8 fix, landed where it
    was owed: the live enrollment path, not a seeding script.

    InsightFace detects, 5-point-aligns and resamples to 112×112 regardless of input size, so a
    phone's 4 MB original buys nothing but transfer time — and `embed_selfie`'s 20 s timeout was
    measured to expire mid-upload on a slow uplink precisely because ~2.4 MB of base64 was in
    flight (`backend/seed.py`'s cast-portrait note). ≤1280 px re-encoded JPEG is ~100 KB. Any
    decode failure returns the original untouched: the face worker's own error is the real one.
    """
    try:
        from io import BytesIO  # noqa: PLC0415

        from PIL import Image, ImageOps  # noqa: PLC0415

        raw = base64.b64decode(selfie_b64, validate=True)
        image = Image.open(BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        if max(image.size) <= 1280 and len(raw) <= 400_000:
            return selfie_b64
        image.thumbnail((1280, 1280))
        out = BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=88)
        return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - see docstring
        return selfie_b64


def _embed_one(selfie_b64: str) -> tuple[list[float], BoundingBox, float]:
    """One face from a selfie, or a 400 — enrollment/re-claim need exactly one clear face."""
    try:
        body = internal.embed_selfie(_shrink_for_embed(selfie_b64), max_faces=1)
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
    """The one identity-granting call in this module — see the module docstring.

    Reachable from exactly two callers, and that is the invariant worth protecting: `review_claim`'s
    approve branch and `redeem_claim_link`. Nothing on the selfie path may call this.
    """
    merge_custom_claims(uid, personId=person_id)
    # `uidLinks` lives under `people/{personId}/private/` (fs.person_private_ref), which the rules
    # deny to every client: it maps anonymous sessions to humans, and the person document itself has
    # to stay member-readable for kiosk credits, the leaderboard and the tier→vipWeight lookup.
    # `set(merge=True)` rather than `update`, because the private doc may not exist yet.
    fs.person_private_ref(event_id, person_id).set(
        {"uidLinks": firestore.ArrayUnion([uid])}, merge=True
    )


def _revoke_identity(event_id: str, uid: str, person_id: str) -> None:
    """Undo `_grant_identity` for one uid — conditionally, and that condition is the whole point.

    `personId` is cleared only if it still points at *this* person. A guest who was approved as
    person A and later made a second, denied enrollment attempt must keep A: the deny is a statement
    about the claim, not about them. Same for `uidLinks` — ArrayRemove of this uid only, so the other
    devices on that album keep working.
    """
    try:
        if custom_claims(uid).get("personId") == person_id:
            merge_custom_claims(uid, personId=None)
    except Exception as exc:  # noqa: BLE001 - a claim we cannot read is one we must not guess at
        log.warn("claim_revoke_failed", uid=uid, person=person_id, err=str(exc))
    try:
        fs.person_private_ref(event_id, person_id).update(
            {"uidLinks": firestore.ArrayRemove([uid])}
        )
    except Exception as exc:  # noqa: BLE001 - the person may already be gone; that is the end state
        log.warn("uidlink_remove_failed", uid=uid, person=person_id, err=str(exc))


@firestore.transactional
def _consume_claim_attempt(
    transaction: firestore.Transaction, event_id: str, uid: str
) -> None:
    """Ban check + per-uid hourly cap before a selfie is embedded, in one transaction.

    Deliberately the same shape as `api/uploads.py::_register_batch`'s first half — the ban flag and
    an hour-bucket counter on `guests/{uid}` — because the enrollment path had neither, and it is the
    more attackable of the two: an upload costs the attacker a photograph, while a selfie submission
    is a free probe against every enrolled face in the event. A banned guest could still enroll, and
    a script could still walk the guest list one selfie at a time.

    Read-then-write in a transaction rather than two statements so two concurrent attempts cannot
    both see count = N; raising inside it aborts the write, so a rejected attempt costs no budget.
    Counted *before* `internal.embed_selfie` for the same reason the upload limit is counted before
    URLs are signed: the point of the limit is to protect the expensive call, not to record that it
    happened.
    """
    guest_ref = fs.guest_ref(event_id, uid)
    snap = guest_ref.get(transaction=transaction)
    guest = (snap.to_dict() or {}) if snap.exists else {}
    if guest.get("banned"):
        raise errors.forbidden("GUEST_BANNED", "this guest cannot enroll at this event")

    now = dt.datetime.now(dt.timezone.utc)
    started = guest.get("claimWindowStartedAt")
    count = int(guest.get("claimWindowCount") or 0)
    if not isinstance(started, dt.datetime) or now - started >= dt.timedelta(hours=1):
        started, count = now, 0
    if count + 1 > CLAIM_RATE_LIMIT_PER_HOUR:
        raise errors.rate_limited(
            f"enrollment limit of {CLAIM_RATE_LIMIT_PER_HOUR} attempts/hour reached",
            retryAfterSeconds=int((started + dt.timedelta(hours=1) - now).total_seconds()),
        )

    transaction.set(
        guest_ref,
        {
            "uid": uid,
            "claimWindowStartedAt": started,
            "claimWindowCount": count + 1,
            "lastSeenAt": fs.SERVER_TIMESTAMP,
            "createdAt": guest.get("createdAt") or fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )


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


def _drop_review_selfie(event_id: str, claim_id: str, selfie_uri: str | None) -> None:
    """Delete the stored review selfie and stop the audit advertising it.

    `ClaimAudit.selfieUri` has always documented itself as "held claims only; deleted when the host
    decides", and nothing was deleting it: an unaltered biometric of every person who ever tried to
    enroll was accumulating in the raw bucket, retained by nothing but the 30-day lifecycle rule
    (spec 02 §5). A decided claim does not need the picture — the decision and its audit trail
    survive, which is what §3.1 layer 2 asks for. The field is nulled in the same breath as the
    object so the document can never point at bytes that are gone.
    """
    parsed = gcs.parse_gs_uri(str(selfie_uri or ""))
    if parsed is not None:
        gcs.delete_object(parsed[0], parsed[1])
    fs.claim_audit_ref(event_id, claim_id).update({"selfieUri": None})


# ---------------------------------------------------------------- enrollment (spec 02 §3)


@router.post("/people", response_model=EnrollResponse)
async def enroll(
    req: EnrollRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> EnrollResponse:
    if not req.biometricConsent:
        raise errors.bad_request("CONSENT_REQUIRED", "biometric consent is required to enroll")

    # Order as in `api/uploads.py`: free validation, then the rate limit, then the expensive call.
    image_bytes = _decode_selfie(req.selfie)
    _consume_claim_attempt(fs.db().transaction(), eventId, principal.uid)
    embedding, _box, _det_score = _embed_one(req.selfie)
    cfg = settings()

    # Impersonation guard (spec 02 §3): does this selfie already belong to someone?
    enrolled = faces_lib.enrolled_people(eventId)
    hits = faces_lib.match_people(eventId, embedding, min_similarity=cfg.tau_claim, people=enrolled)

    if hits:
        # Every match, not just a protected or ambiguous one. The old shape here — protected/ambiguous
        # to the host, anything else straight to `_apply_reclaim` — is the hole this module's docstring
        # describes: `PersonHit.protected` is `tier <= 2 or hostEnrolled`, so an ordinary tier-3
        # guest's album was handed over on similarity alone.
        return _hold_identity_match(
            eventId,
            principal,
            hits[0],
            faces_lib.is_ambiguous(hits),
            image_bytes,
            method=ClaimMethod.ENROLL,
        )

    return _create_person_and_claim(eventId, principal, req, embedding, image_bytes)


def _create_person_and_claim(
    event_id: str,
    principal: Principal,
    req: EnrollRequest,
    embedding: list[float],
    image_bytes: bytes,
) -> EnrollResponse:
    """A selfie that matches nobody enrolled: mint the person, record a held claim, grant nothing.

    The person document and the face template are written immediately — the guest has consented and
    the album has to exist for the host to approve it — but `claimApproved` stays False, no custom
    claim is set, and no face carries `personId` until the host says yes. Sub-threshold and
    over-threshold claims differ only in `holdReason`, so `CLAIM_SIZE` keeps meaning exactly what
    spec 02 §3.1 says (a claim big enough to be worth stealing) instead of becoming a synonym for
    "held".
    """
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
            # Who submitted this selfie. It lives here rather than being derived from `uidLinks`
            # because that field moved into a subcollection, and a subcollection field cannot be
            # queried without a collection-group index — whereas this is one equality filter on an
            # already-deny-all, already-event-scoped collection. `_pending_person_id` is the reader.
            "enrolledByUid": principal.uid,
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )
    fs.person_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "displayName": req.displayName,
            "tier": int(Tier.GUEST),
            "hostEnrolled": False,
            "claimApproved": False,
            "featured": False,
            "consent": {
                "selfieEnrolled": True,
                "enrolledAt": fs.SERVER_TIMESTAMP,
                "retentionNoticeShown": req.retentionNoticeShown,
            },
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )
    # `uidLinks` records whose enrollment this was so the review card, the deny path and the deletion
    # flow can all answer that question. It is not a grant — what unlocks the private album is the
    # `personId` custom claim, which only host approval writes. It sits in `private/` because the
    # person document is member-readable and a uid↔human map is not something a member may read.
    fs.person_private_ref(event_id, person_id).set(
        {"uidLinks": [principal.uid], "tasteProfile": {}}, merge=True
    )

    hits = faces_lib.nearest_faces(
        event_id, embedding, limit=CLAIM_FACE_LIMIT, min_similarity=cfg.tau_claim
    )
    grouped = faces_lib.group_hits(hits)
    face_count = sum(len(v) for v in grouped.values())
    top_similarity = max((h.similarity for h in hits), default=0.0)
    claim_id = new_ulid()

    # `faceIds` and `exemplars` on *every* held claim, not only the over-threshold one. They are what
    # lets approval replay the link later and what the host looks at while deciding; a held claim
    # without them is a decision the host is asked to make blind and cannot act on afterwards.
    selfie_uri = _store_review_selfie(event_id, claim_id, image_bytes)
    _write_audit(
        event_id,
        ClaimAudit(
            claimId=claim_id,
            personId=person_id,
            uid=principal.uid,
            faceCount=face_count,
            topSimilarity=round(top_similarity, 4),
            method=ClaimMethod.ENROLL,
            status=ClaimStatus.HELD,
            holdReason=(
                ClaimHoldReason.CLAIM_SIZE
                if face_count >= cfg.claim_review_threshold
                else ClaimHoldReason.HOST_APPROVAL
            ),
            targetPersonId=person_id,
            createdPerson=True,
            displayName=req.displayName,
            faceIds=grouped,
            exemplars=_build_exemplars(event_id, hits),
            selfieUri=selfie_uri,
        ),
    )
    return EnrollResponse(
        outcome=EnrollOutcome.HELD_FOR_REVIEW,
        personId=person_id,
        displayName=req.displayName,
        claimId=claim_id,
        claimedFaces=0,
        topSimilarity=top_similarity,
        message="the host is confirming it's you — your own uploads are already in your album.",
    )


def _hold_identity_match(
    event_id: str,
    principal: Principal,
    top: faces_lib.PersonHit,
    ambiguous: bool,
    image_bytes: bytes,
    *,
    method: ClaimMethod,
) -> EnrollResponse:
    """A selfie that matches an already-enrolled person — recorded, never granted.

    `method` is a parameter rather than a constant because `reclaim` calls this too and used to be
    audited as `enroll`, which made the one path spec 02 §3 describes as a re-claim invisible under
    that name in the activity feed.
    """
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
            method=method,
            status=ClaimStatus.HELD,
            # Protected always wins even when the top-2 are also within the ambiguity margin —
            # it is the more specific, more actionable signal for the host's review card, and a
            # VIP match must never quietly read as a generic "too close to call". `HOST_APPROVAL`
            # is the remaining case: an ordinary match with no risk signal beyond the fact that
            # somebody is asking for an album that already has an owner.
            holdReason=(
                ClaimHoldReason.PROTECTED_PERSON
                if top.protected
                else ClaimHoldReason.AMBIGUOUS_MATCH
                if ambiguous
                else ClaimHoldReason.HOST_APPROVAL
            ),
            targetPersonId=top.personId,
            createdPerson=False,
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
    """Recover an album on a new device (spec 02 §3.2) — as a request to the host, not a grant.

    A re-claim and the impersonation attempt it is indistinguishable from are the *same request*:
    both are "here is a face, give me that person's album". Only the host can tell them apart, so
    this endpoint's job is to put the question in front of them with the evidence attached.
    """
    if not req.biometricConsent:
        raise errors.bad_request("CONSENT_REQUIRED", "biometric consent is required to re-claim")

    image_bytes = _decode_selfie(req.selfie)
    _consume_claim_attempt(fs.db().transaction(), eventId, principal.uid)
    embedding, _box, _det = _embed_one(req.selfie)
    cfg = settings()
    hits = faces_lib.match_people(
        eventId, embedding, min_similarity=cfg.tau_claim, people=faces_lib.enrolled_people(eventId)
    )
    if not hits:
        raise errors.forbidden("NO_MATCH", "this selfie does not match an enrolled album")
    if faces_lib.is_ambiguous(hits):
        # Still a 403 rather than a hold, and deliberately so: spec 02 §3 declines the auto-claim and
        # falls back to the magic link, and a review card here would name whichever twin happened to
        # score 0.001 higher as the target — inviting the host to approve a link the numbers do not
        # actually support. Enrollment can hold an ambiguous match because the alternative there is
        # minting a duplicate person; here the alternative is a working magic link.
        raise errors.forbidden(
            "AMBIGUOUS_MATCH",
            "too close to call — use your magic link, or ask the host to confirm",
        )
    return _hold_identity_match(
        eventId, principal, hits[0], False, image_bytes, method=ClaimMethod.RECLAIM
    )


# ---------------------------------------------------------------- host enrollment (spec 13 §7)


@router.post("/people/host-enroll", response_model=HostEnrollResponse)
async def host_enroll(
    req: HostEnrollRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> HostEnrollResponse:
    """The host adds a participant with a reference photo — `backend/seed.py::seed_person`
    promoted to a product path (spec 13 §7), with the gates the seed never needed: the same ban
    check and hourly attempt cap the selfie path has (a reference photo is exactly as much of a
    free probe against enrolled faces as a selfie is), and an explicit permission acknowledgment.

    **§4.28's rule survives with no exceptions: this grants no identity.** `claimApproved: True`
    because the host — the approver — is the one enrolling, so the album may accrete (that is the
    point; the director needs to see who has been photographed). But **no uid link and no
    `personId` claim are written here**: when the real human selfie-enrolls, their match against
    this host-enrolled person is *held* (`holdReason=protected_person`, the impersonation guard),
    and host approval remains the only grant. The subject keeps subject-veto and delete-my-data.
    """
    _require_host(principal, eventId)
    event = fs.get_event(eventId)
    if event is None:
        raise errors.not_found("NO_EVENT", "unknown event")
    if not req.photoConsent:
        raise errors.bad_request(
            "CONSENT_REQUIRED", "confirm you have this person's permission to add their photo"
        )
    _decode_selfie(req.photo)  # size/encoding gate, same as the selfie path
    _consume_claim_attempt(fs.db().transaction(), eventId, principal.uid)
    embedding, _box, _det = _embed_one(req.photo)

    person_id = new_ulid()
    now = dt.datetime.now(dt.timezone.utc)
    fs.enrollment_ref(eventId, person_id).set(
        {"personId": person_id, "embedding": embedding, "createdAt": now}
    )
    fs.person_ref(eventId, person_id).set(
        {
            "personId": person_id,
            "displayName": req.displayName,
            "tier": int(req.tier),
            "hostEnrolled": True,
            # The host is the approver, so the approval the self-enrollment path waits for has
            # already happened — same reasoning as `backend/seed.py::seed_person`. Without it
            # `workers/face` would refuse to auto-link any face to this person and the People
            # panel would show a name with a permanently empty album.
            "claimApproved": True,
            "featured": False,
            "consent": {
                "selfieEnrolled": True,
                "enrolledAt": now,
                "retentionNoticeShown": True,
                "hostAsserted": True,  # spec 13 §7: consent asserted by the host, not the subject
            },
            "createdAt": now,
        }
    )
    fs.person_private_ref(eventId, person_id).set({"uidLinks": [], "tasteProfile": {}})
    log.info(
        "host_enrolled",
        event_id=eventId,
        person=person_id,
        tier=int(req.tier),
        by=principal.uid,
    )
    return HostEnrollResponse(personId=person_id, displayName=req.displayName, tier=int(req.tier))


@router.post("/people/{personId}/tier")
async def set_tier(
    req: TierRequest,
    eventId: str = Path(min_length=1, max_length=128),
    personId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Spec 11 §6's promote/demote, finally implemented. Deterministic ranking metadata only —
    "VIP is policy, not memory" (spec 11 §4) — and audited to `ops/`, because who the system
    treats as important is exactly the kind of change a host must be able to point at later."""
    _require_host(principal, eventId)
    ref = fs.person_ref(eventId, personId)
    snap = ref.get()
    if not snap.exists:
        raise errors.not_found("NO_PERSON", "unknown person")
    was = int((snap.to_dict() or {}).get("tier", int(Tier.GUEST)))
    ref.update({"tier": int(req.tier)})
    if was != int(req.tier):
        fs.ops_alert(
            eventId,
            "tier_changed",
            f"host set {personId} tier {was} → {req.tier}",
            severity="info",
            resolved=True,
            by=principal.uid,
            personId=personId,
            fromTier=was,
            toTier=int(req.tier),
        )
    log.info("tier_set", event_id=eventId, person=personId, tier=int(req.tier), by=principal.uid)
    return {"personId": personId, "tier": int(req.tier)}


# ---------------------------------------------------------------- host review (spec 02 §3.1)


def _held_claim(event_id: str, claim_id: str) -> tuple[firestore.DocumentReference, dict[str, Any]]:
    ref = fs.claim_audit_ref(event_id, claim_id)
    snap = ref.get()
    if not snap.exists:
        raise errors.not_found("NO_CLAIM", "unknown claim")
    audit = snap.to_dict() or {}
    if audit.get("status") != ClaimStatus.HELD.value:
        raise errors.conflict("ALREADY_REVIEWED", f"claim is already {audit.get('status')}")
    return ref, audit


def _claim_person_id(audit: dict[str, Any]) -> str:
    """Which person this claim is about: the one it created, or the one it is asking to join."""
    person_id = str(audit.get("personId") or audit.get("targetPersonId") or "")
    if not person_id:
        raise errors.conflict("CLAIM_INCOMPLETE", "this claim names no person")
    return person_id


@router.post("/claims/{claimId}/review", response_model=ClaimReviewResponse)
async def review_claim(
    req: ClaimReviewRequest,
    eventId: str = Path(min_length=1, max_length=128),
    claimId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> ClaimReviewResponse:
    """Approve or deny a held claim — the only place in this module that grants an album.

    Both decisions are *effective*, which is the change from the original shape: approve performs
    the grant that used to happen at enrollment time, and deny undoes everything the attempt left
    behind instead of only stamping a status on the audit document.
    """
    _require_host(principal, eventId)
    ref, audit = _held_claim(eventId, claimId)
    person_id = _claim_person_id(audit)
    uid = str(audit.get("uid") or "")
    created_person = bool(audit.get("createdPerson"))

    if req.decision == "deny":
        return _deny_claim(eventId, claimId, ref, audit, person_id, uid, created_person, principal)

    if not uid:
        # Nothing to grant identity *to*. An audit without a uid is malformed rather than merely
        # incomplete, and approving it would silently do half of a grant.
        raise errors.conflict("CLAIM_INCOMPLETE", "this claim names no uid to grant")

    person_ref = fs.person_ref(eventId, person_id)
    if not person_ref.get().exists:
        # The person can be gone by now: a denied sibling claim deleted it, or the guest used
        # delete-my-data. Approving into a missing document would write a person with one field.
        raise errors.conflict("PERSON_GONE", "the person this claim names no longer exists")

    # The grant, in one place, for both shapes of claim. `_grant_identity` sets the custom claim the
    # security rules read and adds the uid to `uidLinks`; `claimApproved` is what releases the face
    # worker's auto-link (`workers/face/app.py`) for every photo that arrives after this moment.
    _grant_identity(eventId, uid, person_id)
    person_ref.update({"claimApproved": True})
    linked = faces_lib.link_faces(eventId, person_id, audit.get("faceIds") or {}, claimId)
    if not created_person:
        # A device joining an album that already existed is exactly what spec 02 §3.2's notice is
        # for. A first enrollment has no earlier device to warn.
        _new_device_notice(eventId, person_id)
    _drop_review_selfie(eventId, claimId, audit.get("selfieUri"))

    ref.update(
        {
            "status": ClaimStatus.APPROVED.value,
            "personId": person_id,
            "faceCount": linked,
            "reviewedBy": principal.uid,
            "reviewedAt": fs.SERVER_TIMESTAMP,
        }
    )
    log.info("claim_approved", event_id=eventId, claim=claimId, person=person_id, faces=linked)
    return ClaimReviewResponse(
        claimId=claimId, status=ClaimStatus.APPROVED, personId=person_id, linkedFaces=linked
    )


def _deny_claim(
    event_id: str,
    claim_id: str,
    ref: firestore.DocumentReference,
    audit: dict[str, Any],
    person_id: str,
    uid: str,
    created_person: bool,
    principal: Principal,
) -> ClaimReviewResponse:
    """Deny, and actually reverse — a status stamp on its own left the attempt's residue in place.

    Three things go away, in the order that keeps the system consistent if the process dies midway:
    the custom claim (the only thing that unlocks anything), then the person and its face template if
    *this* claim created them, then the stored selfie. `created_person` is load-bearing: denying a
    re-claim must leave the target person entirely alone — that person is who the claim was aimed at,
    and deleting their album on a deny would make the review queue the attack rather than the defence.

    Spec 02 §3.1's original wording for the claim-size gate was "deny → the enrollment stands as a new
    person with zero claimed faces". That sentence belongs to the world where the enrollment itself was
    trusted and only the face links were held. Now that the host is approving the *album*, a deny means
    "this is not who they say they are", and leaving a person document plus a stored face template
    behind would keep an unapproved biometric in the index for the next photo to match against. The
    acceptance criterion it serves — "denial leaves the cluster unclaimed" — still holds exactly: no
    face doc was ever written, so every one of them is still unclaimed.
    """
    if uid:
        _revoke_identity(event_id, uid, person_id)

    person_deleted = False
    if created_person:
        person_ref = fs.person_ref(event_id, person_id)
        person = person_ref.get().to_dict() or {}
        # Refuse to delete a person the host has since approved through some *other* claim (a second
        # device, a magic link). The deny still stands for this uid — the claim above is already
        # revoked — but the album it would have deleted belongs to somebody now.
        if person and person.get("claimApproved"):
            log.warn(
                "claim_deny_kept_person",
                event_id=event_id,
                claim=claim_id,
                person=person_id,
                reason="person approved by another claim",
            )
        else:
            fs.enrollment_ref(event_id, person_id).delete()
            # Firestore does not cascade subcollections, so the private document (`uidLinks`, taste
            # profile) has to be deleted explicitly or a denied enrollment leaves residue behind.
            fs.person_private_ref(event_id, person_id).delete()
            person_ref.delete()
            person_deleted = True

    _drop_review_selfie(event_id, claim_id, audit.get("selfieUri"))
    ref.update(
        {
            "status": ClaimStatus.DENIED.value,
            "reviewedBy": principal.uid,
            "reviewedAt": fs.SERVER_TIMESTAMP,
        }
    )
    log.info(
        "claim_denied",
        event_id=event_id,
        claim=claim_id,
        host=principal.uid,
        person=person_id,
        person_deleted=person_deleted,
    )
    return ClaimReviewResponse(
        claimId=claim_id,
        status=ClaimStatus.DENIED,
        personId=None if person_deleted else person_id,
        linkedFaces=0,
    )


@router.post("/claims/{claimId}/reverse", response_model=ClaimReviewResponse)
async def reverse_claim(
    eventId: str = Path(min_length=1, max_length=128),
    claimId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> ClaimReviewResponse:
    """Undo a claim the host approved by mistake (spec 02 §8's "host 'unlink' reverses it").

    `ClaimStatus.REVERSED` and `faces_lib.unlink_person` were both written for this and had no caller,
    which meant an approval was in practice final — the opposite of spec 02 §3.1 layer 2's promise
    that a wrong claim is "visible and host-reversible, never silent". A legacy `applied` claim (one
    the old automatic re-claim path granted before the host-approves-everything change) is reversible
    here too; that is the population most likely to need it.

    What gets reversed depends, again, on whether the claim minted the person:

    - it did → the album is this claim's doing, so its faces go back to unclaimed and the person
      returns to unapproved. The person document and the face template survive: spec 02 §5's deletion
      flow is a separate, guest-initiated act, and a reversal is not a deletion.
    - it did not → only this uid loses the album. The faces belong to the person the claim joined,
      and unlinking them would empty the victim's album to punish the impostor.

    An audit written before `createdPerson` existed reverses as the second case, deliberately. The two
    legacy shapes are genuinely indistinguishable in the document — the old code wrote
    `personId == targetPersonId` for both a new person and a re-claim of an existing one — and of the
    two possible mistakes, revoking a device link that should have kept its faces is recoverable while
    emptying an innocent person's album is not.
    """
    _require_host(principal, eventId)
    ref = fs.claim_audit_ref(eventId, claimId)
    snap = ref.get()
    if not snap.exists:
        raise errors.not_found("NO_CLAIM", "unknown claim")
    audit = snap.to_dict() or {}
    status = str(audit.get("status") or "")
    if status not in (ClaimStatus.APPROVED.value, ClaimStatus.APPLIED.value):
        raise errors.conflict("NOT_REVERSIBLE", f"only a granted claim can be reversed, not {status}")

    person_id = _claim_person_id(audit)
    uid = str(audit.get("uid") or "")
    unlinked = 0
    if bool(audit.get("createdPerson")):
        unlinked = faces_lib.unlink_person(eventId, person_id)
        try:
            fs.person_ref(eventId, person_id).update({"claimApproved": False})
        except Exception as exc:  # noqa: BLE001 - an already-deleted person is the desired end state
            log.warn("claim_reverse_flag_failed", person=person_id, err=str(exc))
    if uid:
        _revoke_identity(eventId, uid, person_id)

    ref.update(
        {
            "status": ClaimStatus.REVERSED.value,
            "reviewedBy": principal.uid,
            "reviewedAt": fs.SERVER_TIMESTAMP,
        }
    )
    log.info(
        "claim_reversed",
        event_id=eventId,
        claim=claimId,
        person=person_id,
        host=principal.uid,
        faces=unlinked,
    )
    return ClaimReviewResponse(
        claimId=claimId,
        status=ClaimStatus.REVERSED,
        personId=person_id,
        linkedFaces=0,
        unlinkedFaces=unlinked,
    )


# ---------------------------------------------------------------- the review queue (spec 02 §3.1)


def _review_card(event_id: str, claim_id: str, audit: dict[str, Any]) -> ClaimReviewCard:
    """One audit document as a review card, with every `gs://` URI converted at this boundary.

    Neither the selfie nor an exemplar thumbnail can be handed over as stored: every bucket in this
    project has `--public-access-prevention` (deploy/buckets.sh), so a `gs://` string in an `<img
    src>` renders nothing, and opening either bucket to fix that would trade an enforced boundary for
    a UI convention. Both therefore become *API paths*, which the console fetches with its bearer
    token and follows to a short-lived signed URL — the shape `api/media.py` established and
    `frontend/src/lib/useAuthedImage.ts` already consumes. Relative, not absolute, for the same
    reason `mediaRenderPath` is relative: the client knows its own API origin, and baking one in here
    would pin a stored card to whatever `NEXT_PUBLIC_API_URL` happened to be at request time.
    """
    exemplars = []
    for raw in audit.get("exemplars") or []:
        media_id = str(raw.get("mediaId") or "")
        box = raw.get("box")
        exemplars.append(
            ClaimReviewExemplar(
                mediaId=media_id,
                faceId=str(raw.get("faceId") or ""),
                box=BoundingBox(**box) if isinstance(box, dict) and box else None,
                similarity=float(raw.get("similarity") or 0.0),
                thumbUrl=(
                    f"/v1/events/{event_id}/media/{media_id}/render?variant=thumb"
                    # The stored `thumbUri` is not forwarded, only consulted: its presence is how we
                    # know the thumb render actually landed, and a card that offers a link to a
                    # render that was never produced is a broken tile on the host's screen.
                    if media_id and raw.get("thumbUri")
                    else None
                ),
            )
        )
    hold_reason = audit.get("holdReason")
    return ClaimReviewCard(
        claimId=claim_id,
        method=ClaimMethod(str(audit.get("method") or ClaimMethod.ENROLL.value)),
        status=ClaimStatus(str(audit.get("status") or ClaimStatus.HELD.value)),
        holdReason=ClaimHoldReason(str(hold_reason)) if hold_reason else None,
        displayName=audit.get("displayName"),
        faceCount=int(audit.get("faceCount") or 0),
        topSimilarity=float(audit.get("topSimilarity") or 0.0),
        at=audit.get("at"),
        createdPerson=bool(audit.get("createdPerson")),
        selfieUrl=(
            f"/v1/events/{event_id}/claims/{claim_id}/selfie" if audit.get("selfieUri") else None
        ),
        exemplars=exemplars,
    )


@router.get("/claims", response_model=ClaimListResponse)
async def list_claims(
    eventId: str = Path(min_length=1, max_length=128),
    status: str = Query(default=ClaimStatus.HELD.value),
    principal: Principal = Depends(caller),
) -> ClaimListResponse:
    """The host's review queue. Without it, `POST …/claims/{claimId}/review` had nothing to review:
    a held claim was unresolvable by any means short of the Firestore console, so every guard that
    routes a claim to the host was in practice a guard that dropped it.

    Newest first, sorted in Python rather than by `order_by`: an equality filter plus an ordering on
    a second field needs a composite index, spec 09 §3's index inventory has none for `claimAudits`,
    and a review queue is bounded by `CLAIM_LIST_LIMIT` — sorting fifty documents in process costs
    nothing and adds no index to deploy.
    """
    _require_host(principal, eventId)
    try:
        wanted = ClaimStatus(status)
    except ValueError:
        raise errors.bad_request(
            "BAD_STATUS", f"status must be one of: {', '.join(s.value for s in ClaimStatus)}"
        ) from None

    query = fs.claim_audits_col(eventId).where(
        filter=firestore.FieldFilter("status", "==", wanted.value)
    )
    snaps = list(query.limit(CLAIM_LIST_LIMIT).stream())
    cards = [_review_card(eventId, snap.id, snap.to_dict() or {}) for snap in snaps]
    # `at` is absent for a heartbeat of a moment (the server timestamp has not resolved on the write
    # that produced this read), so it sorts as epoch rather than crashing the queue.
    cards.sort(key=lambda c: c.at or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return ClaimListResponse(claims=cards)


@router.get("/claims/{claimId}/selfie")
async def claim_selfie(
    eventId: str = Path(min_length=1, max_length=128),
    claimId: str = Path(min_length=1, max_length=64),
    json: bool = Query(default=False, description="return {url} instead of a 302"),
    principal: Principal = Depends(caller),
) -> Response:
    """302 to a short-lived signed URL for the enrollment selfie on a review card.

    Host-only, re-checked on every request, and with no unauthenticated branch at all — which is the
    one way this differs from `api/media.py::media_render`, whose public branch exists because a
    kiosk is a television. This is a biometric submitted for a decision; there is no viewer of it
    other than the host.

    Which is also why `?json=1` is not optional here but the *only* way this ever reaches a screen:
    host-only means a bearer token, a bearer token means a preflight, and a preflight cannot survive
    the 302 onto `storage.googleapis.com` (see `api/media.py::media_render`). The host console was
    fetching this and getting a CORS failure every time — a review queue that showed no selfie to
    review. It hands back the URL and the console puts it straight in an `<img src>`.
    """
    _require_host(principal, eventId)
    snap = fs.claim_audit_ref(eventId, claimId).get()
    if not snap.exists:
        raise errors.not_found("NO_CLAIM", "unknown claim")
    parsed = gcs.parse_gs_uri(str((snap.to_dict() or {}).get("selfieUri") or ""))
    if parsed is None:
        # Either the claim was decided (the selfie is deleted at that point, by design) or it never
        # carried one.
        raise errors.not_found("NO_SELFIE", "this claim has no stored selfie")
    url = gcs.signed_get_url(
        parsed[0],
        parsed[1],
        ttl_minutes=CLAIM_REVIEW_URL_TTL_MINUTES,
        response_type="image/jpeg",
    )
    log.info("claim_selfie_served", event_id=eventId, claim=claimId, host=principal.uid)
    if json:
        return JSONResponse({"url": url, "expiresInSec": CLAIM_REVIEW_URL_TTL_MINUTES * 60})
    return RedirectResponse(url, status_code=302)


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
    display_name = None
    if person_id:
        # The one remaining path that grants without host review, and it needs none: the link was
        # minted by a session that already held this personId (`create_claim_link` reads it off the
        # caller's own token), so redeeming it proves possession of a secret the album's owner chose
        # to share. Spec 02 §3.1's family-shares-devices case is the whole point of it being
        # multi-use.
        _grant_identity(event_id, principal.uid, str(person_id))
        display_name = (fs.person_ref(event_id, str(person_id)).get().to_dict() or {}).get(
            "displayName"
        )
        # Spec 02 §8: "every claim (enroll/re-claim/magic-link) produces a `claimAudits` entry" —
        # magic links were the one method that granted an album and left no trace in the feed, which
        # also made them the one grant a host could not reverse. `createdPerson` is False: the person
        # existed long before this redemption, so a reversal drops this device and nothing else.
        _write_audit(
            event_id,
            ClaimAudit(
                claimId=new_ulid(),
                personId=str(person_id),
                uid=principal.uid,
                faceCount=0,
                topSimilarity=0.0,
                method=ClaimMethod.MAGIC_LINK,
                status=ClaimStatus.APPLIED,
                targetPersonId=str(person_id),
                createdPerson=False,
                displayName=display_name,
            ),
        )
    log.info("claim_link_redeemed", event_id=event_id, person=person_id, uid=principal.uid)
    return RedeemResponse(eventId=event_id, personId=person_id, displayName=display_name)


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


def _pending_person_id(event_id: str, uid: str) -> str | None:
    """The person this uid enrolled and the host has not approved, if there is exactly one.

    Needed because enrollment no longer grants a `personId` claim: `_require_person` reads that claim,
    so without this a guest whose claim is still in the review queue could not delete the selfie they
    had just submitted — and spec 02 §4 puts "delete button location" on the biometric consent screen
    itself, which makes an undeletable pending enrollment the one failure that would falsify the
    consent copy.

    Keyed off `enrollments/{personId}.enrolledByUid` rather than the person document's `uidLinks`,
    which moved into `people/{personId}/private/profile` when the uid↔human map stopped being
    member-readable. A subcollection field cannot be filtered without a collection-group query, an
    `eventId` field on every private document and a composite index; this is one equality filter on a
    collection that is already event-scoped and already deny-all to every client, so it needs no index
    at all. `claimApproved` is still read off the person document, exactly as before.

    Only an *unapproved* person is reachable this way, and only when this uid is the single such
    person's, so it can never become a second route into somebody else's album: an approved album's
    owner always has the claim and takes the branch above.
    """
    query = fs.enrollments_col(event_id).where(
        filter=firestore.FieldFilter("enrolledByUid", "==", uid)
    )
    # `snap.id` is the personId — `enrollments/{personId}` is keyed by it. `claimApproved` still lives
    # on the person document, so that is a second read per candidate; there is at most a handful,
    # because this only runs on the "delete my data" path for a uid with no `personId` claim.
    pending = [
        snap.id
        for snap in query.stream()
        if not (fs.person_ref(event_id, snap.id).get().to_dict() or {}).get("claimApproved")
    ]
    return pending[0] if len(pending) == 1 else None


def _drop_claim_selfies(event_id: str, uid: str) -> int:
    """Delete every review selfie this uid ever submitted (spec 02 §5's "deletes… embeddings").

    A held claim stores the raw capture for the host's five-second check, so "delete my data" that
    left those objects behind would leave an unaltered biometric of the person who asked to be
    forgotten sitting in the raw bucket until the 30-day lifecycle rule noticed. Queried by `uid`
    alone — one equality filter, no composite index — and decided claims have already had theirs
    dropped, so this is a small loop over that guest's own attempts.
    """
    dropped = 0
    query = fs.claim_audits_col(event_id).where(filter=firestore.FieldFilter("uid", "==", uid))
    for snap in query.stream():
        audit = snap.to_dict() or {}
        if audit.get("selfieUri"):
            _drop_review_selfie(event_id, snap.id, audit.get("selfieUri"))
            dropped += 1
    return dropped


@router.delete("/people/me")
async def delete_me(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> dict[str, Any]:
    person_id = principal.person_id or _pending_person_id(eventId, principal.uid)
    if not person_id:
        raise errors.forbidden("NOT_ENROLLED", "this action requires an enrolled person")
    person_ref = fs.person_ref(eventId, person_id)

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
    # `uidLinks` moved to `people/{personId}/private/profile` — the person document is member-readable
    # and a uid↔human map is not. Deliberately read *before* the private document is deleted below.
    person_private = fs.person_private_ref(eventId, person_id).get().to_dict() or {}
    uid_links = [u for u in (person_private.get("uidLinks") or []) if u]
    for start in range(0, len(uid_links), 30):
        uid_batch = uid_links[start : start + 30]
        query = fs.media_col(eventId).where(
            filter=firestore.FieldFilter("uploaderUid", "in", uid_batch)
        )
        for doc_snap in query.stream():
            recompute_visibility(eventId, doc_snap.id, extra={"deleted": True})

    selfies = 0
    for uid in uid_links:
        try:
            # Clears only `personId` — a uid that also hosts some event keeps that claim.
            merge_custom_claims(uid, personId=None)
        except Exception as exc:  # noqa: BLE001 - claim cleanup must not block the deletion
            log.warn("claim_clear_failed", uid=uid, err=str(exc))
        selfies += _drop_claim_selfies(eventId, uid)
        # Their Web Push registration too (`guests/{uid}/private/push`). A device address is data
        # about them by any reading, and a subscription that outlived "delete my data" would keep
        # buzzing a phone belonging to somebody the system was told to forget. Same reasoning as the
        # `person_private_ref` delete below: Firestore does not cascade subcollections, so the
        # promise is only true if something explicitly writes it.
        push.delete_token(eventId, uid)
    # The caller's own uid, which is not necessarily in `uid_links` — a *pending* enrollment never
    # got one (§4.28: no face match grants anything), and they can still have subscribed to missions.
    push.delete_token(eventId, principal.uid)

    # The face template is a separate document (spec 02 §5's "deletes person doc + embeddings"), so
    # deleting the person is not enough — this is the write that makes the promise true. The private
    # document is the same problem one level down: Firestore does not cascade a subcollection when its
    # parent is deleted, so deleting only the person would leave `uidLinks` and the taste profile
    # behind as orphaned residue — readable by nobody, but still stored, which is not what "delete my
    # data" says.
    fs.enrollment_ref(eventId, person_id).delete()
    fs.person_private_ref(eventId, person_id).delete()
    person_ref.delete()
    log.info(
        "person_deleted",
        event_id=eventId,
        person=person_id,
        faces=sum(len(v) for v in grouped.values()),
        selfies=selfies,
    )
    return {"ok": True, "personId": person_id}
