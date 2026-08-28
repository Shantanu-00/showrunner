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


class RedeemHostRequest(BaseModel):
    code: str


class RedeemHostResponse(BaseModel):
    eventId: str
    eventName: str | None = None


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
