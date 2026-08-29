"""Enrollment, claims and the claim audit trail (spec 02 §3, spec 03 §1).

The vocabulary here is the whole claim-integrity design in miniature:

- a claim is **applied** when it links faces immediately,
- **held** when it is waiting on the host — which, since S15, is *every* claim,
- **approved** / **denied** by that host,
- **reversed** when a host unlinks one after the fact.

Every one of those transitions writes to `claimAudits/{claimId}`, which is what makes a wrong
claim visible and undoable rather than silent (spec 02 §3.1 layer 2).

**`applied` is now a legacy status, not a reachable one.** Spec 02 §3 routed only a *protected*
match to host approval and treated an ordinary match as an automatic re-claim; that left an
anonymous visitor holding a downloaded kiosk photo of a tier-3 guest able to enroll with it and
receive that guest's private album. The gate is therefore no longer "is this person worth
stealing" but "did the host say yes": no face match grants anything on its own, so every
enrollment and every re-claim is written `held` and nothing — no custom claim, no `uidLinks`
entry, no face link — happens until `POST …/claims/{claimId}/review` approves it. The status
stays in this enum because audit documents written before that change still carry it.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .common import BoundingBox, ConsentRing


class ClaimMethod(str, Enum):
    ENROLL = "enroll"
    RECLAIM = "reclaim"
    MAGIC_LINK = "magic_link"


class ClaimStatus(str, Enum):
    APPLIED = "applied"
    HELD = "held"
    APPROVED = "approved"
    DENIED = "denied"
    REVERSED = "reversed"


class ClaimHoldReason(str, Enum):
    #: A first-time enrollment whose claim would link ≥ CLAIM_REVIEW_THRESHOLD faces (spec 02 §3.1).
    CLAIM_SIZE = "claim_size"
    #: The selfie matched a VIP or host-enrolled person — never silently granted (spec 02 §3).
    PROTECTED_PERSON = "protected_person"
    #: Top-2 matches within the ambiguity margin (spec 02 §3 — twins and siblings at a wedding).
    AMBIGUOUS_MATCH = "ambiguous_match"
    #: No risk signal fired — the album simply needs its owner confirmed, because since S15 the host
    #: approves every album (see the module docstring). Kept distinct from `CLAIM_SIZE` so that
    #: reason still means what spec 02 §3.1 says it means: a claim large enough to be worth stealing.
    #: A review card carrying this reason is the low-stakes, one-tap case; the other three are the
    #: ones where the host is being asked to look closely.
    HOST_APPROVAL = "host_approval"


class ClaimExemplar(BaseModel):
    """One tile of the host's review card: the photo, the face in it, and how close the match was."""

    mediaId: str
    faceId: str
    box: BoundingBox | None = None
    similarity: float = 0.0
    thumbUri: str | None = None


class ClaimAudit(BaseModel):
    """`claimAudits/{claimId}` — spec 02 §3.1 requires the first six fields; the rest make a held
    claim reviewable and a bad claim reversible without a database console."""

    claimId: str
    personId: str | None = None  # None while a protected-person claim is pending
    uid: str
    faceCount: int = 0
    topSimilarity: float = 0.0
    method: ClaimMethod
    at: dt.datetime | None = None

    status: ClaimStatus = ClaimStatus.APPLIED
    holdReason: ClaimHoldReason | None = None
    #: The person a protected-person hold would link to, once the host says yes.
    targetPersonId: str | None = None
    #: True when this claim is what minted `people/{personId}` (a first enrollment). It is the field
    #: `deny` and `reverse` branch on, and it exists rather than being inferred from `personId`
    #: because the two reversals are opposites: denying a first enrollment deletes the person it
    #: created, while denying a re-claim must leave the person it was aiming at completely untouched
    #: — that person is the *victim* of the claim, and a deny that deleted their album would turn
    #: the review queue into the attack.
    createdPerson: bool = False
    displayName: str | None = None
    faceIds: dict[str, list[str]] = Field(default_factory=dict)  # mediaId → faceIds
    exemplars: list[ClaimExemplar] = Field(default_factory=list)
    selfieUri: str | None = None  # held claims only; deleted when the host decides
    reviewedBy: str | None = None
    reviewedAt: dt.datetime | None = None


# ---------------------------------------------------------------- wire: enrollment & claims


class EnrollRequest(BaseModel):
    """Biometric consent is explicit and checked server-side (spec 02 §4) — never pre-ticked."""

    selfie: str  # base64 JPEG/PNG from a live camera capture
    displayName: str | None = Field(default=None, max_length=80)
    biometricConsent: bool = False
    retentionNoticeShown: bool = False


class EnrollOutcome(str, Enum):
    #: uid linked to a person immediately. No enrollment or re-claim path returns this any more (see
    #: the module docstring); it stays on the wire because the guest PWA still branches on it and
    #: because the host-approval response is what took its place, not a rename of it.
    LINKED = "linked"
    #: The person document exists and the claim is recorded, but *nothing* is granted until the host
    #: approves: no custom claim, no `uidLinks` entry, no face link. The guest still sees their own
    #: uploads, which reach them through the `uploaderUid` rule and never needed a personId.
    HELD_FOR_REVIEW = "held_for_review"
    #: The selfie matched an already-enrolled person: nothing was created or linked, and the host is
    #: being asked whether this really is that person.
    PENDING_HOST_APPROVAL = "pending_host_approval"


class EnrollResponse(BaseModel):
    outcome: EnrollOutcome
    personId: str | None = None
    displayName: str | None = None
    claimId: str | None = None
    claimedFaces: int = 0
    topSimilarity: float = 0.0
    #: `signInWithCustomToken` on the client; carries the `personId` claim immediately so the
    #: private album renders without waiting for a token refresh.
    customToken: str | None = None
    message: str


class ReclaimRequest(BaseModel):
    """Same explicit biometric consent as `EnrollRequest`, and for the same reason (spec 02 §4).

    Re-claim shipped without this field, which meant the one path that *only* ever compares a live
    capture against stored face templates was the one path that never asked. A returning guest on a
    new device is a new consent moment — the previous device's checkbox is not on this browser, and
    "they consented once on a phone whose storage was cleared" is not a record of anything.
    """

    selfie: str
    biometricConsent: bool = False


class ClaimLinkResponse(BaseModel):
    """The code rides in the URL fragment so it never reaches a server log (spec 02 §3.1)."""

    url: str
    code: str
    expiresAt: dt.datetime


class RedeemRequest(BaseModel):
    code: str


class RedeemResponse(BaseModel):
    eventId: str
    personId: str | None = None
    #: Optional for the same reason it is optional on `EnrollResponse`: this module grants identity by
    #: setting a custom claim on the caller's *existing* anonymous uid (see `api/identity.py`'s module
    #: docstring), so no second sign-in round trip is minted. Declaring it required made every single
    #: magic-link redemption raise a pydantic ValidationError and 500 — the endpoint has always passed
    #: `None`.
    customToken: str | None = None
    displayName: str | None = None


class ClaimReviewRequest(BaseModel):
    decision: Literal["approve", "deny"]


class ClaimReviewResponse(BaseModel):
    claimId: str
    status: ClaimStatus
    personId: str | None = None
    linkedFaces: int = 0
    #: Faces returned to unclaimed by `POST …/claims/{claimId}/reverse`.
    unlinkedFaces: int = 0


# ---------------------------------------------------------------- wire: the host's review queue


class ClaimReviewExemplar(BaseModel):
    """One exemplar tile as the host console can actually load it.

    Same tile as `ClaimExemplar`, with the `gs://` URI replaced by a path the browser can fetch.
    Two models rather than one field that is sometimes a URI and sometimes a URL: the stored form is
    a bucket location no client may resolve, the wire form is an API path, and letting one field
    mean both is how a `gs://` string ends up in an `<img src>`.
    """

    mediaId: str
    faceId: str
    box: BoundingBox | None = None
    similarity: float = 0.0
    #: `GET /v1/events/{eventId}/media/{mediaId}/render?variant=thumb` — the existing host-gated
    #: redirect (`api/media.py`), not a second signing path.
    thumbUrl: str | None = None


class ClaimReviewCard(BaseModel):
    """Everything the host needs for spec 02 §3.1's "five-second visual check", and nothing else.

    No `uid` and no `faceIds`: the host is being asked to recognise a face, and a browser tab that
    does not carry the session identifier or the face-document ids cannot leak them.
    """

    claimId: str
    method: ClaimMethod
    status: ClaimStatus
    holdReason: ClaimHoldReason | None = None
    displayName: str | None = None
    faceCount: int = 0
    topSimilarity: float = 0.0
    at: dt.datetime | None = None
    #: True when approving mints a new album, False when it joins a device to an existing person.
    createdPerson: bool = False
    #: `GET /v1/events/{eventId}/claims/{claimId}/selfie` — host-gated, 302 to a signed URL.
    selfieUrl: str | None = None
    exemplars: list[ClaimReviewExemplar] = Field(default_factory=list)


class ClaimListResponse(BaseModel):
    claims: list[ClaimReviewCard] = Field(default_factory=list)


class ConsentUpdateRequest(BaseModel):
    ring: ConsentRing


class SubjectVetoRequest(BaseModel):
    """Hide me from public — per photo, per subject (spec 02 §4). `hide=False` undoes it."""

    hide: bool = True


class VisibilityResponse(BaseModel):
    mediaId: str
    visibility: str | None = None
