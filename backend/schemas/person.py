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


class Person(BaseModel):
    personId: str
    displayName: str | None = None
    uidLinks: list[str] = Field(default_factory=list)
    selfieEmbedding: list[float] | None = None  # 512-d, unit-norm
    tier: Tier = Tier.GUEST
    hostEnrolled: bool = False  # host-enrolled persons require host approval to be claimed
    featured: bool = False  # audited ranking override only, never a visibility override
    consent: PersonConsent = Field(default_factory=PersonConsent)
    tasteProfile: dict[str, float] = Field(default_factory=dict)  # spec 07
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
