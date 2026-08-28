"""The Event Graph — the document every other agent reasons against.

`status` is the system's master switch (spec 08 §2): there is no per-event infrastructure, so
going live is a Firestore status flip and nothing more. `class` is server-assigned only
(spec 11 §1.1) — accepting it from a client would let anyone escape the capacity cap, TTL and
cost ceiling by asserting `protected_demo`.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class EventStatus(str, Enum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"
    WRAPPING = "wrapping"
    WRAPPED = "wrapped"


#: Statuses in which the API issues upload URLs (spec 08 §2). `wrapping` accepts in-flight
#: outbox items during the grace window; the grace check itself lives with the API handler.
UPLOAD_OPEN_STATUSES = frozenset({EventStatus.LIVE, EventStatus.WRAPPING})


class EventClass(str, Enum):
    PROTECTED_DEMO = "protected_demo"  # exactly one: the judge-mode event, exempt from guardrails
    INTERNAL_DEV = "internal_dev"  # the deployment owner's own sandbox
    PUBLIC = "public"  # everyone else: counted, TTL'd, cost-capped


class EventTemplateId(str, Enum):
    WEDDING_GENERIC = "wedding_generic"
    WEDDING_HINDU = "wedding_hindu"
    WEDDING_CHRISTIAN = "wedding_christian"
    WEDDING_MUSLIM = "wedding_muslim"
    BACHELOR_BACHELORETTE = "bachelor_bachelorette"
    BIRTHDAY = "birthday"
    GRADUATION = "graduation"
    CORPORATE_OFFSITE = "corporate_offsite"
    CUSTOM = "custom"


class VipTopology(str, Enum):
    PYRAMID = "pyramid"  # wedding-style: default guest, host promotes
    FLAT = "flat"  # bachelor-party-style: default inner circle, host demotes


class SensitivityProfile(BaseModel):
    """Host-declared dials (spec 11 §2) — never inferred from a culture label.

    The event-level dial is a **ceiling**: stage context can tighten a Guardian verdict but
    never loosen it past what the host declared.
    """

    pda: str = "context_dependent"  # public_ok | context_dependent | private_only
    alcohol: str = "context_dependent"
    attire: str = "standard"  # relaxed | standard | conservative


class RequiredMoment(BaseModel):
    momentId: str
    label: str
    tierWeight: float = 1.0


class EventTypeProfile(BaseModel):
    templateId: EventTemplateId = EventTemplateId.CUSTOM
    vipTopology: VipTopology = VipTopology.PYRAMID
    sensitivityProfile: SensitivityProfile = Field(default_factory=SensitivityProfile)
    culturalGlossary: list[str] = Field(default_factory=list)
    requiredMomentsTemplate: list[RequiredMoment] = Field(default_factory=list)


class EventStage(BaseModel):
    """A scheduled beat of the event. Windows are UTC; EXIF is interpreted through
    `Event.timezone` before being compared to them (spec 03 §5.1)."""

    stageId: str
    label: str
    startsAt: dt.datetime | None = None
    endsAt: dt.datetime | None = None
    requiredMoments: list[RequiredMoment] = Field(default_factory=list)
    theme: str | None = None  # kiosk palette hint (spec 04 §4)


class DemoConfig(BaseModel):
    """Spec 09 §5. Disclosed in the README — demo conveniences, not a hidden thumb."""

    enabled: bool = False
    compressedTimeline: bool = False
    autoPromoteEnrollees: bool = False  # honoured only when class == protected_demo
    publicFloor: float | None = None  # 0.0 in the demo event; real events use 0.45


class Event(BaseModel):
    eventId: str
    name: str
    timezone: str  # required — EXIF interpretation depends on it (spec 03 §5.1)
    status: EventStatus = EventStatus.DRAFT
    eventClass: EventClass = Field(default=EventClass.PUBLIC, alias="class")

    stages: list[EventStage] = Field(default_factory=list)
    activeStage: str | None = None
    stageOverride: str | None = None  # host override always wins (spec 05 §2)

    eventTypeProfile: EventTypeProfile = Field(default_factory=EventTypeProfile)
    demoConfig: DemoConfig = Field(default_factory=DemoConfig)

    publicFloor: float = 0.45  # spec 04 §2 quality gate
    publicFrozen: bool = False  # PANIC: freeze public (spec 08 §5)

    #: Spec 06 §5's couple-reel opener, cached here because it is generated **once per event**, not
    #: once per reel version: every later `couple` commission reuses this clip instead of paying Veo's
    #: $0.80 again (`directors/reel/opener.py::ensure`). `openerFailed` remembers a permanent failure
    #: (no eligible portrait, a blocked generation) so a persistently-failing event does not retry the
    #: spend on every single commission.
    openerUri: str | None = None
    openerModel: str | None = None
    openerCostUsd: float = 0.0
    openerFailed: str | None = None

    createdAt: dt.datetime | None = None
    liveAt: dt.datetime | None = None
    wrappedAt: dt.datetime | None = None
    costSoFarUsd: float = 0.0

    model_config = {"populate_by_name": True}
