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

from .common import SceneSetting


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
    PROTECTED_DEMO = "protected_demo"  # exactly one: the standing global demo, exempt from guardrails
    INTERNAL_DEV = "internal_dev"  # the deployment owner's own sandbox
    PUBLIC = "public"  # everyone else: counted, TTL'd, cost-capped


class EventAccessMode(str, Enum):
    OPEN = "open"  # anyone with the join link becomes a member
    INVITE = "invite"  # a code is required, and photo bytes stop being unauthenticated


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
    #: Where the host says this stage happens. The world model's cold-start prior: it lets the kiosk's
    #: `onTopic` term know that outdoor photos are *expected* during the baraat before a single baraat
    #: photo has been classified — which the observed distribution cannot possibly know, since at that
    #: moment the corpus is entirely indoors.
    #:
    #: Host-declared, never model-assigned: `/itinerary/parse` proposes it, the host's review table
    #: confirms it, and `PUT …/stages` writes it. Same posture as the cultural glossary and the
    #: sensitivity dials (spec 11 §2) — a *declared* setting cannot be a wrong guess about someone's
    #: event, it can only be a host describing their own venue. `None` is the common case and means
    #: "no prior", not "indoors".
    expectedSetting: SceneSetting | None = None


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

    #: The event's calendar span as ISO **local dates** ("2026-10-12") in `timezone`, not UTC
    #: instants — "Day N" is a wall-clock concept, and a UTC midnight lands on the wrong day for
    #: half the planet. `None` (every event created before spec 13, and any host who skips the
    #: field) means "no day structure": `shared/eventtime.py` returns no day index and every
    #: renderer falls back to the time-only form it always had. Day indices are always **derived**
    #: from these via `shared/eventtime.py`, never stored — a host correcting the start date must
    #: not leave stale day numbers anywhere.
    startsOn: str | None = None
    endsOn: str | None = None
    #: How many *people* the host expects (spec 13) — distinct from `guestCount`, which counts
    #: sessions (one human = several uids, spec 02 §1). Feeds the group-photo coverage threshold
    #: and the invite seat-cap default; `None` disables group-coverage logic entirely.
    expectedParticipants: int | None = None

    stages: list[EventStage] = Field(default_factory=list)
    activeStage: str | None = None
    stageOverride: str | None = None  # host override always wins (spec 05 §2)

    eventTypeProfile: EventTypeProfile = Field(default_factory=EventTypeProfile)
    demoConfig: DemoConfig = Field(default_factory=DemoConfig)

    publicFloor: float = 0.45  # spec 04 §2 quality gate
    publicFrozen: bool = False  # PANIC: freeze public (spec 08 §5)

    #: Volume guardrails, orthogonal to `class`. `None` (every event today) means uncapped — a real
    #: host's party is bounded by the guest list and the venue, not by a number. A standing event
    #: with no natural end (spec 13's global demo) needs its own ceiling instead, and this is a
    #: config value any host could also set (same discipline as `publicFloor`, spec 09 §5), not a
    #: `protected_demo`-only branch. Enforced in `api/uploads.py::_register_batch`, same transaction
    #: as the per-guest rate limit, on net-new `clientMediaId`s only — a retried/re-issued upload
    #: never counts twice.
    dailyMediaCap: int | None = None
    lifetimeMediaCap: int | None = None
    #: Rolling-24h counter mirroring the per-guest `rateWindowStartedAt`/`rateWindowCount` shape
    #: already used above for `INVITE_DEFAULT_SEATS`-style per-uid limiting, just event-scoped.
    dailyMediaCount: int = 0
    dailyMediaWindowStartedAt: dt.datetime | None = None
    lifetimeMediaCount: int = 0

    #: How many net-new media since the last successful reel commission (any persona) — reset to 0
    #: by `directors/reel/commission.py::commission` on success, incremented alongside the caps
    #: above. `reelCommissionEveryNMedia` is the guardrail that reads it: `None` (every event today)
    #: keeps the existing cadence (Story Director judgment + the daily ceiling); set, a commission is
    #: refused until this many new photos/videos have landed since the last one — a standing event
    #: that never wants a highlight reel cut every few minutes sets this instead of fighting the
    #: director's own pacing.
    mediaSinceLastReel: int = 0
    reelCommissionEveryNMedia: int | None = None

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
