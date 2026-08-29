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


class EventAccessMode(str, Enum):
    OPEN = "open"  # anyone with the join link becomes a member
    INVITE = "invite"  # a code is required, and photo bytes stop being unauthenticated


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
    """Spec 09 §5. Disclosed on the `/judge` page itself, not merely in the README — demo
    conveniences, never a hidden thumb (spec 12 §1's design consequence for the architect judge).

    `publicFloor` used to live here as a `protected_demo`-only override of `Event.publicFloor`. It was
    removed in S14: the demo event sets the ordinary `Event.publicFloor` field instead, so no exposure
    decision anywhere depends on which event a viewer is looking at. See
    `shared/visibility.py::public_floor` for the full reasoning.

    `autoPromoteEnrollees` survives, gated to `protected_demo` in the enrollment handler, but the
    seeded judge event ships it **off**: its only purpose is to make a judge feel personally featured,
    the seeded cast's tier-0/1 people demonstrate `vipWeight` just as well, and leaving it on would
    put a judge-conditional branch back on the tour's own path.
    """

    enabled: bool = False
    compressedTimeline: bool = False
    autoPromoteEnrollees: bool = False  # honoured only when class == protected_demo; off on judge_demo


class EventAccess(BaseModel):
    """The door: who may become a member of this event at all.

    A different axis from the consent rings, and the rings need no change. Ring 2 means "this
    event's shared surfaces"; `mode` means "how many people the event has". They compose, which is
    why `shared/visibility.py::recompute_visibility` keeps exactly its existing inputs and stays the
    single writer of `media.visibility` — there is no per-media audience field anywhere.

    Every field here is **host-settable only**, through `POST /v1/events/{eventId}/access*`. None of
    it is ever accepted from a guest path, for the same reason `Event.class` never is (module
    docstring): a guest who could assert `mode: 'open'` would open someone else's event, and a guest
    who could assert `maxGuests` would raise their own seat cap.

    - `mode == 'open'` — anyone holding the join link joins. `POST /join` needs no code.
    - `mode == 'invite'` — `POST /join` requires the code whose sha256 is `codeHash`, and
      `api/media.py`/`api/reels.py` stop serving bytes to non-members (the two places where an
      `<img src>`/`<video src>` would otherwise be unauthenticated by design).

    `codeHash` stores only the hash, exactly like `claimLinks/{hash}` and `hostLinks/{hash}` — the
    third instance of the same machinery, not a new mechanism. Rotating means a fresh hash plus
    `codeRotatedAt`; the old code stops working the instant the hash is replaced.

    `kioskPublic` is honoured by the kiosk *client*, not by a rule: `events/{id}/kiosk/{document}`
    is `allow read: if true` (spec 09 §3, verbatim) and rules cannot consult this document without a
    `get()`. What actually makes a private event's wall dark is that every collection the kiosk
    renders from — `media`, `people`, `guests`, `bounties`, `reels` — is member-gated. See the
    residual-exposure paragraph in `firestore.rules`'s header.
    """

    mode: EventAccessMode = EventAccessMode.OPEN
    #: Seats, not people. Spec 02 §1 deliberately gives one human several uids (phone, laptop,
    #: rescan), so this counts sessions. `None` = uncapped, and that is what a freshly created
    #: `Event` gets here: a refused legitimate guest at a venue is a far worse failure than one
    #: admitted stranger. `api/host.py::set_access_mode` writes a different default,
    #: `INVITE_DEFAULT_SEATS`, the first time a host flips `open → invite` and has never set a cap —
    #: an *uncapped* invite-only event is the one combination nobody actually wants (the point of
    #: going invite-only is to bound the guest list), so the model default and the first-flip default
    #: intentionally disagree.
    maxGuests: int | None = None
    codeHash: str | None = None
    codeRotatedAt: dt.datetime | None = None
    kioskPublic: bool = True


class Event(BaseModel):
    eventId: str
    name: str
    timezone: str  # required — EXIF interpretation depends on it (spec 03 §5.1)
    status: EventStatus = EventStatus.DRAFT
    eventClass: EventClass = Field(default=EventClass.PUBLIC, alias="class")

    #: The membership boundary (spec 02 §1's uid layer, made event-scoped). Server-assigned only,
    #: like `class` above. `guestCount` is maintained transactionally by `POST /join` in the same
    #: transaction that creates `guests/{uid}` — the pattern `host.py::_go_live_txn` already uses
    #: for `platform/liveEventCount` — so the seat cap and the guest roster can never disagree.
    access: EventAccess = Field(default_factory=EventAccess)
    guestCount: int = 0

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
