"""The bounty document — spec 05 §3's lifecycle, and the one place the fleet asks a human for work.

A bounty is the product's only outbound instruction: everything else in the system reacts to photos
that already exist, and this reacts to photos that *do not*. Three shapes in this file matter beyond
their field lists:

- **`dedupeKey`** is stored rather than derived at read time. The guardrail "no duplicate bounty per
  (moment, vip)" (spec 05 §1) has to hold across ticks, restarts and a host pressing "run director
  now" twice, so the key the check uses is a field on the document instead of a convention two call
  sites have to agree about.
- **`submissions` is an array on the bounty, not a subcollection.** Spec 05 §3 writes it that way and
  it is the right call here: the award transaction has to read every prior submission to enforce
  "duplicate submissions keep only the best score", and one document read is cheaper and strictly
  more consistent than a collection scan inside a transaction.
- **`points` is already scaled and clamped.** `basePoints × vipWeight(targetVip)` clamped into
  [50, 300] happens once, in the Act step, before the document is written — so the number a guest
  sees on their banner, the number the kiosk poster shows and the number the award transaction adds
  to `guests/{uid}.points` cannot disagree.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class BountyStatus(str, Enum):
    """Spec 05 §3: `active → (claimed*) → fulfilled | expired`, plus escalation as a distinct state.

    `escalated` is its own status rather than a flag because it is what the publisher keys the kiosk
    takeover off (spec 04 §4) — an ordinary active bounty is already a banner in every pocket, and
    escalation is the director saying that was not enough.
    """

    ACTIVE = "active"
    ESCALATED = "escalated"
    FULFILLED = "fulfilled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


#: Statuses that still accept submissions and still banner on a guest's phone.
OPEN_STATUSES = frozenset({BountyStatus.ACTIVE.value, BountyStatus.ESCALATED.value})


class BountyAudience(str, Enum):
    """Spec 05 §4's targeting, plus spec 13 §6's `assignee`. `nearStage` = guests who uploaded in
    the last 15 minutes; `assignee` = one specific active guest, resolved deterministically in the
    Act step (never by the model), falling back to `all` when the assignment times out unanswered.
    Audience is **delivery, never pay**: whoever submits the fulfilling photo gets the points."""

    ALL = "all"
    NEAR_STAGE = "nearStage"
    TOP_CONTRIBUTORS = "topContributors"
    ASSIGNEE = "assignee"


class SubmissionVerdict(str, Enum):
    """Partial credit is a real outcome, not a rounding of failure (spec 05 §3).

    "Right moment, weak quality" pays less and leaves the bounty open, because the coverage gap is
    genuinely still open — the alternative (all-or-nothing) either lies about the gap or discourages
    the guest who was closest to closing it.
    """

    FULFILLED = "fulfilled"
    PARTIAL = "partial"
    REJECTED = "rejected"


class BountySubmission(BaseModel):
    mediaId: str
    uid: str
    verdict: SubmissionVerdict
    score: float = 0.0
    points: int = 0
    reason: str | None = None
    at: dt.datetime | None = None


class Bounty(BaseModel):
    bountyId: str
    status: BountyStatus = BountyStatus.ACTIVE

    targetStage: str | None = None
    targetMoment: str | None = None
    targetVip: str | None = None  # personId — resolved by the Face Indexer, never by the LLM
    targetVipName: str | None = None
    #: `(stage, moment, vip)` — the guardrail key, stored so it survives restarts.
    dedupeKey: str = ""

    title: str  # the kiosk wanted-poster headline
    #: The guest-facing banner sentence. Stored as `copy` (spec 05 §3's field name, and what the PWA
    #: reads) but declared under another name here because `copy` shadows a `BaseModel` method — the
    #: alias keeps the document honest without pydantic warning on every import.
    guestCopy: str = Field(alias="copy")
    points: int = Field(ge=0)
    basePoints: int = Field(ge=0)
    vipWeight: float = 1.0
    audience: BountyAudience = BountyAudience.ALL

    #: `armed` = fired from the timeline the moment its stage began (spec 05 §2's anticipation half);
    #: `reconciliation` = the tick found a statistical gap. Kept because the two mechanisms have
    #: different failure modes and the wrap report should not blur them.
    source: str = "reconciliation"
    issuedBy: str = "story_director"
    tickId: str | None = None

    #: Spec 13 §6: who this bounty banners for while `audience == assignee`. Resolved by the
    #: deterministic Act step (the most recently active member), cleared by the Expire step at
    #: `assignmentTimeoutAt` (audience flips to `all`). Display routing only — the award
    #: transaction never reads any of these three fields.
    assigneeUid: str | None = None
    assignedAt: dt.datetime | None = None
    assignmentTimeoutAt: dt.datetime | None = None

    kioskTakeover: bool = False
    submissions: list[BountySubmission] = Field(default_factory=list)
    awardedTotal: int = 0

    createdAt: dt.datetime | None = None
    expiresAt: dt.datetime | None = None
    escalatedAt: dt.datetime | None = None
    fulfilledAt: dt.datetime | None = None
    expiredAt: dt.datetime | None = None

    model_config = {"populate_by_name": True}


def dedupe_key(stage: str | None, moment: str | None, vip: str | None) -> str:
    """One string for the (moment, vip) uniqueness guardrail. Stage is included because the same
    moment in two different stages is two different gaps (a `group_photo` at the Haldi and at the
    reception are not the same missing photograph)."""
    return f"{stage or '-'}|{moment or '-'}|{vip or '-'}"


class BountyCheck(BaseModel):
    """The validator's contract (spec 05 §3), and deliberately narrow.

    It answers only the question no deterministic check can: does this photograph, as the Curator
    described it, actually show the moment the bounty asked for. **Identity is not on this list** —
    `targetVip` is settled by the Face Indexer's `personId` match, and quality by the Curator's
    already-stored `aestheticScore`. A language model that could also decide *who is in the frame*
    would be a language model deciding who gets paid.
    """

    matchesMoment: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
