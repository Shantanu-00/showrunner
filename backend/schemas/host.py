"""Host console & lifecycle wire shapes (spec 08, spec 11 §1/§2/§6).

`EVENT_TEMPLATE_DEFAULTS` is the wizard's "time-saving starting point, never a silent authority"
(spec 11 §2): every dial and glossary term below is exactly what the host sees in the editable
review step and can change in either direction before Go Live. Only `wedding_generic`,
`wedding_hindu`, `wedding_christian`, `bachelor_bachelorette`, `birthday`, `graduation` and
`corporate_offsite` are tabled in spec 11 §2; `wedding_muslim` and `custom` are not, and this file
says so rather than silently inventing a culturally-specific default for the one spec left open —
`wedding_muslim` gets the same *shape* as `wedding_christian` (pyramid, public-facing ceremony)
with its own moment labels, and `custom` starts from the neutral, most-permission-seeking defaults
and leans on the host filling in everything else.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from schemas.event import (
    EventAccessMode,
    EventStage,
    EventTemplateId,
    EventTypeProfile,
    RequiredMoment,
    SensitivityProfile,
    VipTopology,
)


def _profile(
    topology: VipTopology,
    pda: str,
    alcohol: str,
    attire: str,
    moments: list[str],
) -> EventTypeProfile:
    return EventTypeProfile(
        vipTopology=topology,
        sensitivityProfile=SensitivityProfile(pda=pda, alcohol=alcohol, attire=attire),
        culturalGlossary=list(moments),
        requiredMomentsTemplate=[
            RequiredMoment(momentId=label.lower().replace(" ", "_"), label=label) for label in moments
        ],
    )


#: Spec 11 §2's table, plus the two templates it leaves open (see module docstring). `attire` is
#: not spec-tabled at all — "standard" everywhere except the two events where a different default
#: is obviously less wrong (a bachelor party runs relaxed, a keynote runs conservative), flagged
#: here rather than silently chosen, same discipline as the other NOT-spec-pinned constants.
EVENT_TEMPLATE_DEFAULTS: dict[EventTemplateId, EventTypeProfile] = {
    EventTemplateId.WEDDING_GENERIC: _profile(
        VipTopology.PYRAMID, "context_dependent", "public_ok", "standard",
        ["vows", "ring exchange", "first dance", "bouquet toss"],
    ),
    EventTemplateId.WEDDING_HINDU: _profile(
        VipTopology.PYRAMID, "context_dependent", "context_dependent", "standard",
        ["haldi", "sangeet", "pheras", "kanyadaan", "vidaai"],
    ),
    EventTemplateId.WEDDING_CHRISTIAN: _profile(
        VipTopology.PYRAMID, "public_ok", "public_ok", "standard",
        ["processional", "vows", "ring exchange", "first dance"],
    ),
    EventTemplateId.WEDDING_MUSLIM: _profile(
        VipTopology.PYRAMID, "public_ok", "context_dependent", "standard",
        ["nikah", "rukhsati", "walima"],
    ),
    EventTemplateId.BACHELOR_BACHELORETTE: _profile(
        VipTopology.FLAT, "public_ok", "public_ok", "relaxed", [],
    ),
    EventTemplateId.BIRTHDAY: _profile(
        VipTopology.PYRAMID, "public_ok", "context_dependent", "standard",
        ["cake cutting", "toast"],
    ),
    EventTemplateId.GRADUATION: _profile(
        VipTopology.PYRAMID, "public_ok", "private_only", "standard",
        ["stage crossing", "family portrait"],
    ),
    EventTemplateId.CORPORATE_OFFSITE: _profile(
        VipTopology.PYRAMID, "private_only", "context_dependent", "conservative",
        ["keynote", "team sessions"],
    ),
    EventTemplateId.CUSTOM: _profile(
        VipTopology.PYRAMID, "context_dependent", "context_dependent", "standard", [],
    ),
}


# ---------------------------------------------------------------- event creation (spec 08 §1)


class CreateEventRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)  # IANA name; EXIF is interpreted through it
    templateId: EventTemplateId = EventTemplateId.WEDDING_GENERIC
    #: Honoured only when the caller carries `platformAdmin` (spec 11 §1.1). Silently ignored
    #: otherwise — never a 403, because a public caller asserting this and being told exactly why
    #: it didn't work is a hint about the admin gate that this system should not hand out.
    intendedClass: str | None = None


class CreateEventResponse(BaseModel):
    eventId: str
    hostLink: str  # revocable, 30-day, for co-hosts (spec 08 §1)
    recoveryCode: str  # long-lived, printable, for "lost every host device"


# ---------------------------------------------------------------- host links (spec 08 §1)


class HostLinkResponse(BaseModel):
    url: str
    code: str
    expiresAt: dt.datetime


class HostLinkSummary(BaseModel):
    """One link, as much of it as can honestly be shown after the fact.

    There is no `url` and no `code` here, and that is the design rather than an omission: only the
    sha256 of a code is ever stored, so the plaintext cannot be reproduced — which is exactly the
    property that makes a Firestore dump yield no working links. A lost code is rotated, not recovered.
    `linkId` is that hash, which is safe to hand an authenticated host and saves bolting a second
    identifier onto link documents that already exist in the wild.
    """

    linkId: str
    grants: str  # 'host' (co-host link, recovery code) | 'member' (kiosk link)
    recovery: bool = False
    createdAt: dt.datetime | None = None
    expiresAt: dt.datetime | None = None
    revoked: bool = False
    revokedAt: dt.datetime | None = None
    #: Neither revoked nor past its expiry — the only field the console needs to decide "does this
    #: still let someone in", computed server-side so two clocks cannot disagree about it. A client
    #: filtering on `revoked`/`revokedAt` alone would show an expired link as live.
    active: bool = True


class HostLinkListResponse(BaseModel):
    links: list[HostLinkSummary] = Field(default_factory=list)


class RecoveryCodeResponse(BaseModel):
    """The one and only time a freshly minted recovery code is ever readable.

    A dedicated shape rather than `HostLinkResponse`, because a recovery code has no URL: it is typed
    into `/host`, which resolves the event from the code itself. Returning an empty `url` field would
    invite a client to render it.
    """

    recoveryCode: str
    expiresAt: dt.datetime
    #: How many previously-live recovery codes this call revoked. Surfaced so the console can say so
    #: out loud — a host who regenerates should know the old code just stopped working.
    supersededCount: int = 0


class RedeemHostRequest(BaseModel):
    code: str


class RedeemHostResponse(BaseModel):
    eventId: str
    eventName: str | None = None


# ------------------------------------------------------- the door: access mode & seats (spec 02 §1)


class AccessModeRequest(BaseModel):
    """`POST /v1/events/{eventId}/access`.

    `confirm` is required only for `invite → open`, and the endpoint refuses without it. That flip
    widens who can be admitted to read photographs **guests already shared**, which is an exposure
    change made by someone other than the uploader — so the host has to have seen a sentence naming
    the consequence, and the flip is written to `ops/` either way. `open → invite` is free: the door
    shuts, existing members keep the claim they already hold, and rotating the code kills old links.
    """

    mode: EventAccessMode
    #: Only meaningful with `mode == 'invite'`; `None` leaves whatever the event already had (or the
    #: generous default on the first flip). Explicitly settable to `null` via `/access/seats`.
    maxGuests: int | None = Field(default=None, ge=1, le=100_000)
    confirm: bool = False


class SeatsRequest(BaseModel):
    """`POST /v1/events/{eventId}/access/seats` — raising the cap must be one tap, because the
    failure mode is the bride's mother locked out at the venue. `null` removes the cap entirely."""

    maxGuests: int | None = Field(default=None, ge=1, le=100_000)


class KioskPublicRequest(BaseModel):
    """`POST /v1/events/{eventId}/access/kiosk` — the host's "keep this off the wall" switch. A client
    contract, not a rule: `events/{id}/kiosk/{document}` is `allow read: if true` (spec 09 §3) and a
    rule cannot read this field without a `get()`."""

    kioskPublic: bool


class AccessResponse(BaseModel):
    eventId: str
    mode: EventAccessMode
    maxGuests: int | None = None
    guestCount: int = 0
    #: Present only in the response to a rotation or a first flip to `invite` — the plaintext code is
    #: never stored (only its sha256, `host.py::_code_hash`) and therefore can never be re-read.
    joinCode: str | None = None
    joinUrl: str | None = None
    codeRotatedAt: dt.datetime | None = None
    kioskPublic: bool = True


# ---------------------------------------------------------------- wizard (spec 08 §3, spec 11 §2)


class ProfileUpdateRequest(BaseModel):
    templateId: EventTemplateId
    #: When absent, the template's own defaults apply verbatim; present fields override them —
    #: this is the "editable, not silently authoritative" step (spec 11 §2).
    vipTopology: VipTopology | None = None
    sensitivityProfile: SensitivityProfile | None = None
    culturalGlossary: list[str] | None = None
    requiredMomentsTemplate: list[RequiredMoment] | None = None


class ParseItineraryRequest(BaseModel):
    rawText: str = Field(min_length=1, max_length=8000)


class SaveStagesRequest(BaseModel):
    stages: list[EventStage] = Field(min_length=1)


# ---------------------------------------------------------------- lifecycle (spec 08 §2, spec 11 §6)


class LifecycleResponse(BaseModel):
    eventId: str
    status: str
    liveAt: dt.datetime | None = None
    wrappedAt: dt.datetime | None = None


class StageOverrideRequest(BaseModel):
    stageId: str | None = None  # null clears the override — back to the schedule/evidence fusion


class FreezeRequest(BaseModel):
    frozen: bool


# ---------------------------------------------------------------- wrap report (spec 08 §2 step 3)


class StageGap(BaseModel):
    stageId: str
    stageLabel: str
    momentId: str
    momentLabel: str


class StageReportRow(BaseModel):
    stageId: str
    label: str
    photoCount: int
    highlightCount: int
    meanAesthetic: float


class Contributor(BaseModel):
    uid: str
    displayName: str | None = None
    points: int


class WrapReport(BaseModel):
    eventId: str
    generatedAt: dt.datetime
    headline: str
    totalPhotos: int
    totalReels: int
    totalPhotographers: int
    perStage: list[StageReportRow] = Field(default_factory=list)
    honestGaps: list[StageGap] = Field(default_factory=list)
    topContributors: list[Contributor] = Field(default_factory=list)


# ---------------------------------------------------------------- console summary


class ConsoleSummary(BaseModel):
    eventId: str
    status: str
    photos: int
    guests: int
    coveragePct: float
    costSoFarUsd: float
    publicFrozen: bool
    liveEventCount: int | None = None  # only meaningful for class=='public'; else None
    #: How many photos are waiting on the host's call (`guardian.verdict == 'host_review'` with no
    #: `hostDecision` yet), and how many the explicit-content gate blocked. Both are counted by
    #: `api/moderation.py::pending_review_count`, which is also what the review-queue endpoint lists —
    #: one predicate, so the badge and the page can never disagree. `blockedCount` is separate because
    #: the two need different actions: a held photo is a decision, a blocked one is a deletion.
    reviewCount: int = 0
    blockedCount: int = 0
