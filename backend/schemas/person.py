"""People and guests.

A `uid` is a browser session; a `personId` is a human at the event; `uidLinks` joins them
(spec 02 §1). VIP-ness is a `tier` on the person doc — deliberately *not* an array on the event
(spec 11 §3): VIP is policy (deterministic), not memory (probabilistic).
"""

from __future__ import annotations

import datetime as dt
from enum import IntEnum

from pydantic import BaseModel, Field


class Tier(IntEnum):
    """Spec 11 §3. Guest is the default under `pyramid` topology; the host promotes."""

    PRINCIPAL = 0
    INNER_CIRCLE = 1
    NAMED_VIP = 2
    GUEST = 3


#: Deterministic kiosk/reel ranking multiplier per tier (spec 04 §4, spec 11 §3.3).
#: Max across the faces in frame — a guest photographed with a Principal inherits the ×3.0.
VIP_WEIGHT: dict[int, float] = {
    Tier.PRINCIPAL: 3.0,
    Tier.INNER_CIRCLE: 1.8,
    Tier.NAMED_VIP: 1.3,
    Tier.GUEST: 1.0,
}


class PersonConsent(BaseModel):
    """Biometric consent is explicit and never pre-ticked (spec 02 §4)."""

    selfieEnrolled: bool = False
    enrolledAt: dt.datetime | None = None
    retentionNoticeShown: bool = False


class Enrollment(BaseModel):
    """`events/{eventId}/enrollments/{personId}` — the selfie face template, and nothing else.

    Spec 03 §1 sketches this field on the person document. It is stored separately because Firestore
    security rules grant or deny whole documents: the person document has to be readable by other
    guests (kiosk uploader credits, the leaderboard's display names, the deterministic tier→vipWeight
    lookup behind Highlights ranking), and a rule that allows that would also hand every guest a copy
    of everyone's biometric. No client rule grants this collection at all.
    """

    personId: str
    embedding: list[float]  # 512-d, unit-norm
    createdAt: dt.datetime | None = None


class PersonPrivate(BaseModel):
    """`events/{eventId}/people/{personId}/private/profile` — everything about a person that a fellow
    guest may not read.

    Same reasoning as `Enrollment` above, one level down. The person document has to stay
    member-readable (kiosk uploader credits, the leaderboard's display names, the tier→vipWeight
    lookup behind Highlights ranking), and Firestore cannot grant a document while withholding one of
    its fields — so anything private has to be a different document. `firestore.rules` denies
    `people/{personId}/private/**` to every client including the host.

    Two things landed here when the event boundary went in: `uidLinks`, because a uid↔human map hands
    a reader the ability to attribute every anonymous upload to a named person; and the taste fields,
    because a Gemma-authored memo about someone's taste is the most personal text the system holds and
    it was previously readable by anyone who could reach the event.
    """

    #: The uid↔person join table (spec 02 §1). A person may hold many uids — phone, laptop, a rescan
    #: after clearing storage. Never a grant on its own: what unlocks an album is the `personId`
    #: custom claim, which only host approval writes.
    uidLinks: list[str] = Field(default_factory=list)
    tasteProfile: dict[str, float] = Field(default_factory=dict)  # spec 07 §2.1, deterministic
    #: Spec 07 §2.2 — Gemma's 3-sentence memo, recomputed every 15 new reactions. Off the critical
    #: path: nothing reads this to gate anything, only to explain a ranking or steer a personal reel.
    tasteMemo: str | None = None
    tasteMemoAt: dt.datetime | None = None
    #: `reactionCount` at the memo's last write — the threshold `directors/story/taste.py::pending`
    #: compares the live count against, so a memo fires again only after 15 *more* reactions.
    lastMemoReactionCount: int = 0


class Person(BaseModel):
    personId: str
    displayName: str | None = None
    tier: Tier = Tier.GUEST
    hostEnrolled: bool = False  # host-enrolled persons require host approval to be claimed
    #: Has the host confirmed that this album belongs to whoever enrolled it? False on every
    #: self-enrolled person until `POST …/claims/{claimId}/review` says approve, True on anyone the
    #: host seeded themselves. Two things read it and both are album-growing: `api/identity.py`
    #: grants the `personId` custom claim only on approval, and `workers/face` refuses to auto-link
    #: new faces to an unapproved person. That second gate is the load-bearing one — the worker
    #: matches at τ_match (0.45), looser than τ_claim and with no protection check, so without it a
    #: pending enrollment would quietly accrete an album while the host had not yet said yes, and
    #: approving would then hand over photographs the host never saw on the review card.
    claimApproved: bool = False
    featured: bool = False  # audited ranking override only, never a visibility override
    consent: PersonConsent = Field(default_factory=PersonConsent)
    createdAt: dt.datetime | None = None
    #: `uidLinks`, `tasteProfile`, `tasteMemo`, `tasteMemoAt` and `lastMemoReactionCount` are NOT on
    #: this model on purpose — see `PersonPrivate` above. Everything on this document is readable by
    #: any member of the event, so adding a field here is a decision to publish it.


class Guest(BaseModel):
    """Per-uid record: points for the bounty leaderboard, upload counters, ban flag."""

    uid: str
    personId: str | None = None
    points: int = 0
    uploads: int = 0
    banned: bool = False
    #: Rolling upload rate-limit window (spec 01 §3): start of the current hour bucket + count.
    rateWindowStartedAt: dt.datetime | None = None
    rateWindowCount: int = 0
    #: The same hour-bucket window for enrollment / re-claim attempts, deliberately its own pair of
    #: fields rather than a share of the upload budget: they bound different abuses (300 photos an
    #: hour is a busy guest, 300 selfies an hour is somebody hunting for a face that matches), and
    #: folding them together would let an attacker's probing be hidden inside a legitimate upload
    #: burst — or, worse, let one guest's photo batch lock them out of enrolling.
    claimWindowStartedAt: dt.datetime | None = None
    claimWindowCount: int = 0
    createdAt: dt.datetime | None = None
