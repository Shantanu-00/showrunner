"""Enrollment, claims and the claim audit trail (spec 02 §3, spec 03 §1).

The vocabulary here is the whole claim-integrity design in miniature:

- a claim is **applied** when it links faces immediately,
- **held** when the claim-size gate or a protected person routes it to the host,
- **approved** / **denied** by that host,
- **reversed** when a host unlinks one after the fact.

Every one of those transitions writes to `claimAudits/{claimId}`, which is what makes a wrong
claim visible and undoable rather than silent (spec 02 §3.1 layer 2).
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
    #: uid linked to a person; custom token attached.
    LINKED = "linked"
    #: Person exists and the uid is linked, but the face links wait on the host (claim-size gate).
    HELD_FOR_REVIEW = "held_for_review"
    #: The selfie matched a VIP / host-enrolled person: nothing was created or linked.
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
    selfie: str


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
    customToken: str
    displayName: str | None = None


class ClaimReviewRequest(BaseModel):
    decision: Literal["approve", "deny"]


class ClaimReviewResponse(BaseModel):
    claimId: str
    status: ClaimStatus
    personId: str | None = None
    linkedFaces: int = 0


class ConsentUpdateRequest(BaseModel):
    ring: ConsentRing


class SubjectVetoRequest(BaseModel):
    """Hide me from public — per photo, per subject (spec 02 §4). `hide=False` undoes it."""

    hide: bool = True


class VisibilityResponse(BaseModel):
    mediaId: str
    visibility: str | None = None
