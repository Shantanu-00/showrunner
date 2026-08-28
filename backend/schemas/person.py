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


class Person(BaseModel):
    personId: str
    displayName: str | None = None
    uidLinks: list[str] = Field(default_factory=list)
    tier: Tier = Tier.GUEST
    hostEnrolled: bool = False  # host-enrolled persons require host approval to be claimed
    featured: bool = False  # audited ranking override only, never a visibility override
    consent: PersonConsent = Field(default_factory=PersonConsent)
    tasteProfile: dict[str, float] = Field(default_factory=dict)  # spec 07 §2.1, deterministic
    #: Spec 07 §2.2 — Gemma's 3-sentence memo, recomputed every 15 new reactions. Off the critical
    #: path: nothing reads this to gate anything, only to explain a ranking or steer a personal reel.
    tasteMemo: str | None = None
    tasteMemoAt: dt.datetime | None = None
    #: `reactionCount` at the memo's last write — the threshold `directors/story/taste.py::pending`
    #: compares the live count against, so a memo fires again only after 15 *more* reactions.
    lastMemoReactionCount: int = 0
    createdAt: dt.datetime | None = None


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
    createdAt: dt.datetime | None = None
