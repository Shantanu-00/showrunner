"""The host console & event lifecycle (spec 08, spec 11 §1/§2/§6) — the only human operator this
system has.

Descoped deliberately (EXECUTION-PLAN §7 S10) to four things plus the master switch itself:
itinerary paste → Model Armor → structured parse → host-reviewed stage table; Go Live and the rest
of the lifecycle state machine; the "Now: ▶ stage" override; and the wrap-up report. Cut from this
session: the coverage heat-grid, the review-queue UI (`POST /media/{id}/review` already exists —
curl it on camera), thumbs up/down, suggestion cards. Two more land here anyway because they are
one-line, panic-critical, and named as *persistent-header* controls in spec 12 §5.4/§8: `freeze`
and the host-link/creation plumbing without which there is no host claim to console with.

Two routers, same split as `identity.py` and for the same reason: `create_router` (`/v1`) holds the
two endpoints that precede any event — creating one, and redeeming a host link/recovery code — and
`router` (`/v1/events/{eventId}`) holds everything a host with a claim already in hand can do.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Path
from google.adk.agents import LlmAgent
from google.cloud import firestore
from google.genai import types

from schemas.common import GuardianVerdict, SceneSetting
from schemas.event import (
    Event,
    EventAccessMode,
    EventClass,
    EventStatus,
    EventTemplateId,
)
from schemas.host import (
    AccessModeRequest,
    AccessResponse,
    Contributor,
    ConsoleSummary,
    CreateEventRequest,
    CreateEventResponse,
    DetailsUpdateRequest,
    EVENT_TEMPLATE_DEFAULTS,
    FreezeRequest,
    ITINERARY_FILE_MIMES,
    ITINERARY_IMAGE_MAX_BYTES,
    ITINERARY_PDF_MAX_BYTES,
    HostLinkListResponse,
    HostLinkResponse,
    HostLinkSummary,
    KioskPublicRequest,
    LifecycleResponse,
    ParseItineraryRequest,
    ProfileUpdateRequest,
    RecoveryCodeResponse,
    RedeemHostRequest,
    RedeemHostResponse,
    SaveStagesRequest,
    SeatsRequest,
    StageGap,
    StageOverrideRequest,
    StageReportRow,
    WrapReport,
)
from schemas.itinerary_out import ItineraryParseOut
from schemas.wrap_out import WrapHeadlineOut
from directors.story import diary as diary_mod
from services import armor, gemini
# One-way: `moderation` imports nothing from this package, so the console badge can share the
# review queue's own predicate rather than restating it. Do not add the reverse import.
from . import moderation
from shared import coverage, errors, fs, internal, log, spend
from shared import stages as stages_lib
from shared.eventtime import EventCalendar
from shared.auth import (
    Principal,
    TooManyEventClaims,
    caller,
    grant_event_claim,
)
from shared.settings import INVITE_DEFAULT_SEATS, settings
from shared.ulid import new_ulid

create_router = APIRouter(prefix="/v1", tags=["host"])
router = APIRouter(prefix="/v1/events/{eventId}", tags=["host"])

_HOST_LINK_TTL_DAYS = 30
#: The printable recovery code outlives ordinary co-host links by an order of magnitude — it is
#: the answer to "lost every host device", not a thing meant to be shared casually, and a host who
#: loses it has no other way back in.
_RECOVERY_CODE_TTL_DAYS = 365


def _require_host(principal: Principal, event_id: str) -> None:
    if not (principal.is_host_of(event_id) or principal.platform_admin):
        raise errors.forbidden("HOST_ONLY", "this action requires the host")


def _event_or_404(event_id: str) -> dict[str, Any]:
    event = fs.get_event(event_id)
    if event is None:
        raise errors.not_found("NO_EVENT", "unknown event")
    return event


def _count(query: Any) -> int:
    """A Firestore count aggregation, same shape as `directors/story/ledger.py::_count` — billed
    per 1,000 index entries rather than per document. Never raises: a KPI header or a wrap report
    that failed to render because one aggregate hiccuped would be worse than showing a 0."""
    try:
        result = query.count().get()
        return int(result[0][0].value)
    except Exception as exc:  # noqa: BLE001
        log.warn("host_count_failed", err=str(exc))
        return 0


def _nudge_publisher(event_id: str, reason: str) -> None:
    """Best-effort refresh. The event-document listener already reacts to every write this module
    makes (§4.20's publisher lease + listener design); this only covers the judging-month case
    where the publisher is scaled to zero and no listener is running to react at all."""
    try:
        internal.nudge_publisher(event_id, reason=reason)
    except internal.PublisherError as exc:
        log.warn("host_publisher_nudge_failed", event_id=event_id, reason=reason, err=str(exc))


# ==================================================================================== event creation


def _rate_limit_create(uid: str) -> None:
    """Spec 08 §1: "unauthenticated create, rate-limited" — no number pinned, so
    `EVENT_CREATE_RATE_LIMIT_PER_HOUR` (settings.py) is this session's flagged-not-pinned choice.
    A plain read-then-write rather than a transaction: the failure mode of a race here is "one caller
    gets one extra event in a burst", not a security or money-path property this system promises.
    """
    ref = fs.event_creation_limiter_ref(uid)
    now = dt.datetime.now(dt.timezone.utc)
    doc = ref.get().to_dict() or {}
    started = doc.get("windowStartedAt")
    count = int(doc.get("count") or 0)
    if not isinstance(started, dt.datetime) or (now - started) > dt.timedelta(hours=1):
        ref.set({"windowStartedAt": now, "count": 1})
        return
    if count >= settings().event_create_rate_limit_per_hour:
        raise errors.rate_limited("too many events created recently — try again in a bit")
    ref.set({"count": firestore.Increment(1)}, merge=True)


def _apply_template(template_id: EventTemplateId) -> dict[str, Any]:
    profile = EVENT_TEMPLATE_DEFAULTS.get(template_id) or EVENT_TEMPLATE_DEFAULTS[EventTemplateId.CUSTOM]
    payload = profile.model_dump(mode="json")
    payload["templateId"] = template_id.value
    return payload


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _grant_host(uid: str, event_id: str) -> None:
    """Append to the `hosts` array claim, or 409 at the claim ceiling (`shared/auth.py`)."""
    try:
        grant_event_claim(uid, "hosts", event_id)
    except TooManyEventClaims as exc:
        raise errors.conflict("TOO_MANY_EVENTS", str(exc)) from exc


def _mint_host_link(
    event_id: str,
    *,
    ttl_days: int,
    recovery: bool,
    grants: str = "host",
    path: str = "host",
    param: str = "hostCode",
) -> tuple[str, str, dt.datetime]:
    """One hashed, revocable, expiring link document, used for three different grants.

    `grants` is what the redeemer gets: `'host'` for a co-host link or a recovery code, `'member'`
    for a kiosk link (the venue TV needs event membership to render anything once `isMember(eventId)`
    is real, and it is emphatically not a host). Only the sha256 is stored, exactly like
    `claimLinks/{hash}` — a database dump does not yield working links — and the redeeming endpoint
    checks `grants` before it grants anything, so a kiosk link can never be redeemed for a console.
    """
    code = secrets.token_urlsafe(16)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=ttl_days)
    fs.host_link_ref(_code_hash(code)).set(
        {
            "eventId": event_id,
            "expiresAt": expires_at,
            "revoked": False,
            "recovery": recovery,
            "grants": grants,
            "createdAt": fs.SERVER_TIMESTAMP,
        }
    )
    origin = settings().app_origin or "http://localhost:3000"
    url = f"{origin.rstrip('/')}/{path}/{event_id}?{param}={code}"
    return url, code, expires_at


@create_router.post("/events", response_model=CreateEventResponse)
async def create_event(
    req: CreateEventRequest, principal: Principal = Depends(caller)
) -> CreateEventResponse:
    """Spec 08 §1 + spec 11 §1.1: `class` is always server-assigned, never taken from the body."""
    if principal.platform_admin:
        # The deployment owner's own sandbox usage is exactly what spec 11 §1.1 says this path
        # is for ("use the normal /host wizard exactly like any host would") — rate-limiting it
        # the same as an anonymous stranger would make the owner's own dev workflow the thing
        # the limiter throttles first.
        event_class = (
            EventClass.PROTECTED_DEMO
            if req.intendedClass == EventClass.PROTECTED_DEMO.value
            else EventClass.INTERNAL_DEV
        )
    else:
        _rate_limit_create(principal.uid)
        event_class = EventClass.PUBLIC

    if (req.startDate is None) != (req.endDate is None):
        raise errors.bad_request("DATE_RANGE", "a dated event needs both a start and an end date")
    if req.startDate and req.endDate and req.endDate < req.startDate:
        raise errors.bad_request("DATE_RANGE", "the end date is before the start date")

    event_id = new_ulid()
    now = dt.datetime.now(dt.timezone.utc)
    event = Event(
        eventId=event_id,
        name=req.name,
        timezone=req.timezone,
        status=EventStatus.DRAFT,
        startsOn=req.startDate,
        endsOn=req.endDate,
        expectedParticipants=req.expectedParticipants,
        createdAt=now,
        **{"class": event_class},
    )
    payload = fs.to_firestore(event.model_dump(by_alias=True))
    payload["eventTypeProfile"] = _apply_template(req.templateId)

    join_code: str | None = None
    if req.accessMode == EventAccessMode.INVITE:
        # The door set at creation, so an invite-only trip is never open for the minutes between
        # the wizard and the access panel. Same machinery as `set_access_mode`; the seat default
        # derives from the expected head-count when there is one — ×3 because seats count sessions,
        # not people (spec 02 §1: phone + laptop + a rescan), clamped into a sane band. Flagged in
        # HANDOFF §9 like `INVITE_DEFAULT_SEATS` itself; neither number is spec-pinned.
        join_code, code_hash = _new_invite_code()
        seats = (
            max(10, min(INVITE_DEFAULT_SEATS, req.expectedParticipants * 3))
            if req.expectedParticipants
            else INVITE_DEFAULT_SEATS
        )
        payload["access"] = {
            "mode": EventAccessMode.INVITE.value,
            "maxGuests": seats,
            "codeHash": code_hash,
            "codeRotatedAt": now,
            "kioskPublic": payload.get("access", {}).get("kioskPublic", True),
        }
    fs.event_ref(event_id).set(payload)

    # The creator is already an authenticated principal, not a stranger following a link — granting
    # `hosts` directly here is the same "identity on the caller's own uid" discipline spec 02 §1 uses
    # for enrollment, and it means the person who just filled in the wizard is never bounced through
    # their own magic link to reach the console they are already looking at.
    #
    # An *append*, never an assignment: `hosts` used to be a scalar `host` claim, so creating a
    # second event silently revoked the console of the first one — the host was still the host in
    # Firestore and locked out by their own token (`shared/auth.py`'s module docstring).
    _grant_host(principal.uid, event_id)

    host_link, _code, _exp = _mint_host_link(event_id, ttl_days=_HOST_LINK_TTL_DAYS, recovery=False)
    _recovery_link, recovery_code, _rexp = _mint_host_link(
        event_id, ttl_days=_RECOVERY_CODE_TTL_DAYS, recovery=True
    )
    log.info(
        "event_created",
        event_id=event_id,
        host=principal.uid,
        cls=event_class.value,
        dated=bool(req.startDate),
        invite=join_code is not None,
    )
    return CreateEventResponse(
        eventId=event_id, hostLink=host_link, recoveryCode=recovery_code, joinCode=join_code
    )


@router.post("/host-links", response_model=HostLinkResponse)
async def create_host_link(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> HostLinkResponse:
    """Co-host links (spec 08 §1) — revocable, 30 days, minted by an existing host."""
    _require_host(principal, eventId)
    url, code, expires_at = _mint_host_link(eventId, ttl_days=_HOST_LINK_TTL_DAYS, recovery=False)
    return HostLinkResponse(url=url, code=code, expiresAt=expires_at)


@router.get("/host-links", response_model=HostLinkListResponse)
async def list_host_links(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> HostLinkListResponse:
    """Every link ever minted for this event, so spec 08 §1's "all revocable" has a surface.

    **No `url` and no `code`, for any link, ever.** Only the sha256 is stored (`_mint_host_link`), so
    the plaintext genuinely cannot be reproduced here — which is the property that makes a database
    dump worthless and is therefore not a gap to close. What a host gets is enough to *decide*: what
    the link grants, when it was made, when it expires, and whether it still works. Losing a code
    means rotating it, not recovering it.

    `linkId` is the hash. Handing it to an authenticated host discloses nothing — sha256 is one-way,
    and this endpoint already requires the `hosts` claim for this event — and it means revocation needs
    no second identifier bolted onto documents that already exist in the wild.
    """
    _require_host(principal, eventId)
    _event_or_404(eventId)
    now = dt.datetime.now(dt.timezone.utc)
    links: list[HostLinkSummary] = []
    query = fs.db().collection("hostLinks").where(
        filter=firestore.FieldFilter("eventId", "==", eventId)
    )
    for snap in query.stream():
        doc = snap.to_dict() or {}
        expires_at = doc.get("expiresAt")
        expired = not isinstance(expires_at, dt.datetime) or now > expires_at
        links.append(
            HostLinkSummary(
                linkId=snap.id,
                grants=str(doc.get("grants") or "host"),
                recovery=bool(doc.get("recovery")),
                createdAt=doc.get("createdAt"),
                expiresAt=expires_at if isinstance(expires_at, dt.datetime) else None,
                revoked=bool(doc.get("revoked")),
                revokedAt=doc.get("revokedAt") if isinstance(doc.get("revokedAt"), dt.datetime) else None,
                active=not bool(doc.get("revoked")) and not expired,
            )
        )
    # Newest first, and undated documents last rather than crashing the sort — `createdAt` is a server
    # timestamp, so a document read in the same millisecond it was written can still carry None.
    links.sort(key=lambda l: (l.createdAt is not None, l.createdAt), reverse=True)
    return HostLinkListResponse(links=links)


@router.post("/host-links/{linkId}/revoke", response_model=HostLinkSummary)
async def revoke_host_link(
    eventId: str = Path(min_length=1, max_length=128),
    linkId: str = Path(min_length=16, max_length=128),
    principal: Principal = Depends(caller),
) -> HostLinkSummary:
    """Kill one link. Idempotent, and scoped: a host can only revoke links for their own event.

    The `eventId` check is not decoration. `hostLinks` is a root collection keyed by hash, so without
    it any host could revoke any other event's links by guessing nothing at all — they would only need
    a hash, and the listing endpoint above hands hashes out.

    Revocation does not touch anyone who already redeemed the link: their `hosts` claim is minted on
    their own uid and outlives the link, which is spec 08 §1's model (links are revocable, granted
    access is revoked by removing the claim). Worth stating because "revoke" reads like it should
    eject people, and it does not.
    """
    _require_host(principal, eventId)
    ref = fs.host_link_ref(linkId)
    snap = ref.get()
    if not snap.exists:
        raise errors.not_found("NO_LINK", "no such link")
    doc = snap.to_dict() or {}
    if str(doc.get("eventId") or "") != eventId:
        raise errors.not_found("NO_LINK", "no such link")
    ref.update({"revoked": True, "revokedAt": fs.SERVER_TIMESTAMP})
    log.info("host_link_revoked", event_id=eventId, link=linkId[:12], host=principal.uid)
    expires_at = doc.get("expiresAt")
    return HostLinkSummary(
        linkId=linkId,
        grants=str(doc.get("grants") or "host"),
        recovery=bool(doc.get("recovery")),
        createdAt=doc.get("createdAt"),
        expiresAt=expires_at if isinstance(expires_at, dt.datetime) else None,
        revoked=True,
        revokedAt=dt.datetime.now(dt.timezone.utc),
        active=False,
    )


def _revoke_recovery_links(event_id: str) -> int:
    """Revoke every still-active recovery link for this event; returns how many.

    One equality filter, then `recovery` sifted in Python. Two equality filters would be the natural
    query and it is deliberately not written that way: Firestore's need for a composite index across
    two fields is version- and shape-dependent, and a query that starts failing with
    FAILED_PRECONDITION the first time this runs is not a thing to discover on demo day. There are a
    handful of links per event, so the filter is free.

    Shared by the self-service and platform-admin regeneration endpoints below — both need the exact
    same "kill every prior recovery code" step, and a link-revocation bug should have one home.
    """
    superseded = 0
    query = fs.db().collection("hostLinks").where(
        filter=firestore.FieldFilter("eventId", "==", event_id)
    )
    for snap in query.stream():
        doc = snap.to_dict() or {}
        if not doc.get("recovery") or doc.get("revoked"):
            continue
        snap.reference.update({"revoked": True, "revokedAt": fs.SERVER_TIMESTAMP})
        superseded += 1
    return superseded


@router.post("/recovery-code", response_model=RecoveryCodeResponse)
async def regenerate_recovery_code(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> RecoveryCodeResponse:
    """Mint a fresh 365-day recovery code, revoking the previous one.

    The old code cannot be shown again — only its hash was ever stored — so "show me my recovery code"
    is not a request this system can honour, and the console says so. Replacing it is the honest
    equivalent, and it closes the failure spec 08 §1 admits to in its own comment: the code is
    displayed exactly once, at creation, and a host who closed that tab had no way back in.

    The previous recovery code is revoked in the same call rather than left alive. Two valid recovery
    codes for one event is a strictly worse security posture than one, and a host who regenerates has
    by definition lost the old one.

    Requires a live `hosts` claim — a host who has lost every device that ever held one cannot reach
    this endpoint at all, which is exactly the case `admin_regenerate_recovery_code` below exists for.
    """
    _require_host(principal, eventId)
    _event_or_404(eventId)

    superseded = _revoke_recovery_links(eventId)
    _url, code, expires_at = _mint_host_link(
        eventId, ttl_days=_RECOVERY_CODE_TTL_DAYS, recovery=True
    )
    log.info(
        "recovery_code_regenerated", event_id=eventId, host=principal.uid, superseded=superseded
    )
    return RecoveryCodeResponse(
        recoveryCode=code, expiresAt=expires_at, supersededCount=superseded
    )


@router.post("/admin/recovery-code", response_model=RecoveryCodeResponse)
async def admin_regenerate_recovery_code(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> RecoveryCodeResponse:
    """The escape hatch for a host who has lost every device, and the recovery code with it.

    `regenerate_recovery_code` above needs a live `hosts` claim, which is precisely what such a host
    no longer has — there is otherwise no way back into an event that a claim, not a password,
    protects. `platformAdmin` only, and deliberately **not** `_require_host`: a co-host of the event
    is not enough, because the whole point is minting a credential nobody at the event can currently
    produce.

    Same hashed-code machinery and the same supersede-then-mint shape as the self-service endpoint —
    `_revoke_recovery_links` is the one difference-free step, so a link-revocation fix lands in both
    places at once. The one thing this path adds is the `ops/` alert: an admin minting a host
    credential for someone else's event is exactly the kind of action that must be on the record,
    whether or not the host who eventually receives the code asked for it themselves.
    """
    if not principal.platform_admin:
        raise errors.forbidden("PLATFORM_ADMIN_ONLY", "this action requires the platform operator")
    _event_or_404(eventId)

    superseded = _revoke_recovery_links(eventId)
    _url, code, expires_at = _mint_host_link(
        eventId, ttl_days=_RECOVERY_CODE_TTL_DAYS, recovery=True
    )
    log.info(
        "recovery_code_admin_regenerated",
        event_id=eventId,
        admin=principal.uid,
        superseded=superseded,
    )
    fs.ops_alert(
        eventId,
        "recovery_code_admin_regenerated",
        f"platform admin {principal.uid} minted a new host recovery code for this event",
        severity="warning",
        by=principal.uid,
        supersededCount=superseded,
    )
    return RecoveryCodeResponse(
        recoveryCode=code, expiresAt=expires_at, supersededCount=superseded
    )


@create_router.post("/host-claim", response_model=RedeemHostResponse)
async def redeem_host_link(
    req: RedeemHostRequest, principal: Principal = Depends(caller)
) -> RedeemHostResponse:
    """Redeems a host magic link or recovery code onto the caller's own uid (spec 08 §1)."""
    code_hash = _code_hash(req.code)
    snap = fs.host_link_ref(code_hash).get()
    if not snap.exists:
        raise errors.forbidden("BAD_CODE", "this link is invalid")
    link = snap.to_dict() or {}
    expires_at = link.get("expiresAt")
    if link.get("revoked") or not isinstance(expires_at, dt.datetime) or (
        dt.datetime.now(dt.timezone.utc) > expires_at
    ):
        raise errors.forbidden("EXPIRED_CODE", "this link has expired or was revoked")

    # A kiosk link lives in the same collection and is redeemed by `POST /join`, not here: it grants
    # `members`, and a link that hangs on a venue TV for a weekend must never be a route to a console.
    if str(link.get("grants") or "host") != "host":
        raise errors.forbidden("BAD_CODE", "this link is invalid")

    event_id = str(link.get("eventId") or "")
    _grant_host(principal.uid, event_id)
    event = fs.get_event(event_id) or {}
    log.info("host_link_redeemed", event_id=event_id, uid=principal.uid)
    return RedeemHostResponse(eventId=event_id, eventName=event.get("name"))


@router.post("/kiosk-links", response_model=HostLinkResponse)
async def create_kiosk_link(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> HostLinkResponse:
    """A link that gives the venue TV event **membership** — not a host claim.

    Once `isMember(eventId)` is a real boundary, an invite-only event's kiosk renders nothing without
    it: `media`, `people`, `guests`, `bounties` and `reels` are all member-gated. The playlist
    document itself stays world-readable (spec 09 §3, verbatim `allow read: if true`) and that
    residual is stated out loud in `firestore.rules`'s header rather than papered over — it carries
    mediaIds, stage ids and score factors, no names and no bytes.

    Same hashed-code machinery as a co-host link, one field different (`grants: 'member'`), so it is
    revocable and expiring for free and `POST /v1/host-claim` refuses it.
    """
    _require_host(principal, eventId)
    _event_or_404(eventId)
    url, code, expires_at = _mint_host_link(
        eventId,
        ttl_days=_HOST_LINK_TTL_DAYS,
        recovery=False,
        grants="member",
        path="kiosk",
        param="joinCode",
    )
    log.info("kiosk_link_minted", event_id=eventId, by=principal.uid)
    return HostLinkResponse(url=url, code=code, expiresAt=expires_at)


# ==================================================== the door: access mode, invite code, seats
#
# Spec 02 §1 gives the event a membership boundary; nothing pins how a host operates it, so these
# three endpoints are this session's flagged-not-pinned surface. All of them are host-only and none
# of their values is ever accepted from a guest path — the same discipline `class` carries in
# `schemas/event.py`. The invite code is the *third* instance of the sha256-hashed-code machinery in
# this file (`_code_hash` + `_mint_host_link`, already used by co-host links and recovery codes, and
# by `POST /v1/claim` for album links), deliberately not a fourth mechanism.


#: Shown to the host, verbatim, before an `invite → open` flip is accepted, and repeated in the 409
#: when `confirm` is missing so the client cannot show a softer sentence than the server requires.
#: The flip widens who may be *admitted* to read photographs guests have **already** shared, which is
#: an exposure change made by someone other than the uploader — so it is confirmed, audited to `ops/`,
#: and reversible, and the per-photo padlock (spec 02 §4) remains each guest's own remedy. It does
#: *not* rewrite any stored `visibility`: `recompute_visibility` keeps exactly its existing inputs and
#: stays the single writer of that field.
OPEN_FLIP_CONSEQUENCE = (
    "Photos your guests already shared become reachable by anyone who joins this event's link. "
    "Nothing already private becomes public, and each guest keeps their per-photo padlock — but "
    "the door stops asking for a code. This change is recorded in your event's activity log."
)


def _join_url(event_id: str, code: str | None) -> str:
    origin = (settings().app_origin or "http://localhost:3000").rstrip("/")
    return f"{origin}/join/{event_id}" + (f"?joinCode={code}" if code else "")


def _access_of(event: dict[str, Any]) -> dict[str, Any]:
    """The event's `access` map, defaulted for every event created before this field existed."""
    access = dict(event.get("access") or {})
    if str(access.get("mode") or "") not in (EventAccessMode.OPEN.value, EventAccessMode.INVITE.value):
        # Absent (every event created before this field existed) or unrecognised. Both read as `open`,
        # because a value nobody wrote must not become an unexplained lockout.
        access["mode"] = EventAccessMode.OPEN.value
    access.setdefault("maxGuests", None)
    access.setdefault("codeHash", None)
    access.setdefault("kioskPublic", True)
    return access


def _access_response(event_id: str, access: dict[str, Any], event: dict[str, Any], *, code: str | None = None) -> AccessResponse:
    return AccessResponse(
        eventId=event_id,
        mode=EventAccessMode(str(access.get("mode") or EventAccessMode.OPEN.value)),
        maxGuests=access.get("maxGuests"),
        guestCount=int(event.get("guestCount") or 0),
        joinCode=code,
        joinUrl=_join_url(event_id, code) if code else None,
        codeRotatedAt=access.get("codeRotatedAt"),
        kioskPublic=bool(access.get("kioskPublic", True)),
    )


def _new_invite_code() -> tuple[str, str]:
    """A fresh join code and its hash. Only the hash is ever written; the plaintext is returned once
    and cannot be re-read, which is why rotation is the only recovery from a lost code."""
    code = secrets.token_urlsafe(9)
    return code, _code_hash(code)


@router.post("/access", response_model=AccessResponse)
async def set_access_mode(
    req: AccessModeRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> AccessResponse:
    """Flip the door. Both directions work; only one of them needs a confirmation.

    `open → invite` is free — the door shuts, everyone who already joined keeps the `members` claim
    they hold (revoking live claims would eject the guests standing in the room), and the freshly
    minted code is what stops a previously-shared link from still admitting strangers.

    `invite → open` requires `confirm: true` against `OPEN_FLIP_CONSEQUENCE`. Either direction writes
    an `ops/` record, because "who could see this event" is exactly the kind of change a host needs
    to be able to point at afterwards.
    """
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    access = _access_of(event)
    was = str(access.get("mode"))
    target = req.mode.value
    code: str | None = None

    if target == EventAccessMode.INVITE.value:
        if was != EventAccessMode.INVITE.value or not access.get("codeHash"):
            code, access["codeHash"] = _new_invite_code()
            access["codeRotatedAt"] = dt.datetime.now(dt.timezone.utc)
        access["mode"] = EventAccessMode.INVITE.value
        if req.maxGuests is not None:
            access["maxGuests"] = req.maxGuests
        elif access.get("maxGuests") is None:
            access["maxGuests"] = INVITE_DEFAULT_SEATS
    else:
        if was == EventAccessMode.INVITE.value and not req.confirm:
            raise errors.conflict("CONFIRM_REQUIRED", OPEN_FLIP_CONSEQUENCE)
        access["mode"] = EventAccessMode.OPEN.value
        # The hash goes with the mode. Leaving a live code on an open event means a link the host
        # believes they retired still admits people the moment they flip back.
        access["codeHash"] = None
        access["codeRotatedAt"] = dt.datetime.now(dt.timezone.utc)

    fs.event_ref(eventId).update({"access": fs.to_firestore(access)})
    if was != access["mode"]:
        fs.ops_alert(
            eventId,
            "access_mode_changed",
            f"host changed event access from {was} to {access['mode']}",
            severity="info",
            resolved=True,
            by=principal.uid,
            fromMode=was,
            toMode=access["mode"],
        )
    log.info("access_mode_set", event_id=eventId, mode=access["mode"], was=was, by=principal.uid)
    return _access_response(eventId, access, event, code=code)


@router.post("/access/code", response_model=AccessResponse)
async def rotate_invite_code(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> AccessResponse:
    """Rotate the invite code: a new hash plus `codeRotatedAt`, and every link built on the old code
    stops working at that instant. Members who already joined are untouched — they hold a claim, not
    a code, which is the whole reason the claim exists."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    access = _access_of(event)
    if str(access.get("mode")) != EventAccessMode.INVITE.value:
        raise errors.conflict("NOT_INVITE_ONLY", "this event is open — there is no code to rotate")
    code, access["codeHash"] = _new_invite_code()
    access["codeRotatedAt"] = dt.datetime.now(dt.timezone.utc)
    fs.event_ref(eventId).update({"access": fs.to_firestore(access)})
    fs.ops_alert(
        eventId,
        "invite_code_rotated",
        "host rotated this event's invite code — links built on the old code no longer work",
        severity="info",
        resolved=True,
        by=principal.uid,
    )
    log.info("invite_code_rotated", event_id=eventId, by=principal.uid)
    return _access_response(eventId, access, event, code=code)


@router.post("/access/kiosk", response_model=AccessResponse)
async def set_kiosk_public(
    req: KioskPublicRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> AccessResponse:
    """"Don't put this event on a wall at all."

    Honoured by the kiosk *client*, not by a security rule, and the console says so: the playlist
    document is `allow read: if true` (spec 09 §3, verbatim) and a rule cannot consult this field
    without a `get()`, which `firestore.rules` forbids for a reason property 1 of its header explains.
    What actually keeps a private event off a screen is that every collection the kiosk renders from is
    member-gated, so a non-member's kiosk shows an empty programme. This switch is the host's intent,
    stated where the kiosk can read it — not the enforcement, which lives in the rules.
    """
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    access = _access_of(event)
    access["kioskPublic"] = req.kioskPublic
    fs.event_ref(eventId).update({"access": fs.to_firestore(access)})
    log.info("kiosk_public_set", event_id=eventId, kiosk_public=req.kioskPublic, by=principal.uid)
    return _access_response(eventId, access, event)


@router.post("/access/seats", response_model=AccessResponse)
async def set_seats(
    req: SeatsRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> AccessResponse:
    """Raise, lower or remove the seat cap. **Seats, not people:** spec 02 §1 deliberately gives one
    human several uids (phone, laptop, a rescan after clearing site data), so this counts sessions and
    will always read higher than a guest list. `null` removes the cap.

    Lowering below the current count is allowed and is deliberately *not* retroactive: nobody is
    ejected, the door simply stops admitting new sessions. Ejecting a guest mid-event because a host
    typed a smaller number is not a behaviour worth building.
    """
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    access = _access_of(event)
    access["maxGuests"] = req.maxGuests
    fs.event_ref(eventId).update({"access": fs.to_firestore(access)})
    log.info("seats_set", event_id=eventId, seats=req.maxGuests, by=principal.uid)
    return _access_response(eventId, access, event)


# ==================================================================================== wizard (spec 08 §3)


@router.post("/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Spec 11 §2's presets + editable dials, with spec 13's mutability split: the **sensitivity
    dials alone** are editable while the event runs — they are ceilings (a dial can only hold a
    photo back, never release one already judged; Guardian verdicts are stored per-photo, so a
    loosened dial affects only future photos), and a host tightening one mid-event is a safety
    action the console must not refuse. Everything else on the profile (glossary, moment
    templates, topology, preset swaps) stays draft-only, the original discipline: that text rides
    into per-photo prompts and bounty arming, and a live event's cultural context is not
    something a mid-event edit should silently change under the perception pipeline's feet."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    if event.get("status") != EventStatus.DRAFT.value:
        dials_only = (
            req.sensitivityProfile is not None
            and req.vipTopology is None
            and req.culturalGlossary is None
            and req.requiredMomentsTemplate is None
        )
        if not dials_only:
            raise errors.conflict(
                "NOT_DRAFT",
                "after Go Live only the sensitivity dials may change; the glossary, required "
                "moments and topology are fixed",
            )
        profile = dict(event.get("eventTypeProfile") or {})
        profile["sensitivityProfile"] = req.sensitivityProfile.model_dump(mode="json")
        fs.event_ref(eventId).update({"eventTypeProfile": profile})
        fs.ops_alert(
            eventId,
            "sensitivity_dials_changed",
            "host changed the sensitivity dials mid-event (applies to future photos only)",
            severity="info",
            resolved=True,
            by=principal.uid,
            dials=profile["sensitivityProfile"],
        )
        log.info("profile_dials_updated", event_id=eventId, by=principal.uid)
        return {"eventTypeProfile": profile}

    # `culturalGlossary` and `requiredMomentsTemplate[].label` are the two pieces of *this* endpoint's
    # free text that ride straight into a per-photo prompt: `workers/curate/agent.py::event_context`
    # prints every required-moment label and `culturalElements` may only use a glossary term (spec 11
    # §2). `services/armor_plugin.py`'s own reasoning for exempting the perception workers from a
    # per-call Model Armor check is that this text is "already checked at onboarding" — true for the
    # itinerary paste (`parse_itinerary` below calls `armor.guard`), but this endpoint set the
    # glossary without ever calling it. One guard call here is what makes that claim actually true,
    # and it costs nothing on the 8/s classify path this text is calibrated against, because it runs
    # once per wizard save rather than once per photo.
    guarded = ", ".join(req.culturalGlossary or []) + " " + ", ".join(
        m.label for m in (req.requiredMomentsTemplate or [])
    )
    if guarded.strip():
        try:
            armor.guard(guarded, surface="event_profile", event_id=eventId)
        except armor.ArmorBlocked as exc:
            raise errors.bad_request(
                "TEXT_REJECTED",
                f"the glossary or required-moment labels looked like {', '.join(exc.filters) or 'a policy match'}",
            ) from exc

    profile = _apply_template(req.templateId)
    if req.vipTopology is not None:
        profile["vipTopology"] = req.vipTopology.value
    if req.sensitivityProfile is not None:
        profile["sensitivityProfile"] = req.sensitivityProfile.model_dump(mode="json")
    if req.culturalGlossary is not None:
        profile["culturalGlossary"] = req.culturalGlossary
    if req.requiredMomentsTemplate is not None:
        profile["requiredMomentsTemplate"] = [
            m.model_dump(mode="json") for m in req.requiredMomentsTemplate
        ]
    fs.event_ref(eventId).update({"eventTypeProfile": profile})
    log.info("profile_updated", event_id=eventId, template=req.templateId.value)
    return {"eventTypeProfile": profile}


ITINERARY_INSTRUCTION = """\
You turn an event itinerary — pasted text, or a PDF/photographed schedule — into a structured
stage table. One JSON object per the schema.

Extract each distinct phase of the event as one stage, in the order it occurs. For each stage: a
short lowercase snake_case stageId, a human label taken from the host's own words, the time-of-day
text exactly as written (timeHint), and any named required moments within it (momentId snake_case,
label as written).

proposedStartLocal / proposedEndLocal: "YYYY-MM-DDTHH:MM" in the event's own timezone, and ONLY
when you were given the event's date range AND the source states or plainly implies which day the
stage falls on ("Day 3", "Oct 14", "Tuesday", or a single-day event where every time is that day).
A multi-day source that never says which day a stage is on gets empty strings — these values
prefill the host's review picker, so a wrong guess is worse than a blank. When only a start time is
stated, propose an end on the same day using the next stage's start, and leave the last stage's end
empty rather than inventing one. Never propose an instant outside the given date range.

expectedSetting: where this stage will physically happen, but ONLY when the text says or plainly
implies it. One of: indoor_venue, outdoor_venue, outdoor_nature, domestic_interior, vehicle, street.
Leave it empty for anything else, including anything you are inferring from the kind of event rather
than from the words in front of you. "Lawn ceremony" is outdoor_venue; "baraat procession from the
hotel" is street; "shinkansen to Kyoto" is vehicle; "getting ready in the suite" is
domestic_interior; a bare "Reception, 8 PM" says nothing about where, so leave it empty. An empty
value is the correct and common answer.

sourceText: when the input is a document or an image, the plain schedule text you read out of it,
verbatim. When the input is already pasted text, leave it empty.

If the source has no clear stage boundaries, return the whole thing as one stage and add a warning
saying so. Never invent a stage, moment, time or date the source does not imply. Preserve the
host's own language for labels — do not translate, rename or reinterpret a tradition.
"""


def _itinerary_agent() -> LlmAgent:
    return LlmAgent(
        name="itinerary_parser",
        description="Extracts a stage/moment table from a pasted, unstructured event itinerary.",
        model=gemini.adk_model(settings().model_classifier),
        instruction=ITINERARY_INSTRUCTION,
        output_schema=ItineraryParseOut,
        output_key="itinerary",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )


def _coerce_proposed_instants(out: ItineraryParseOut, event: dict[str, Any], event_id: str) -> None:
    """Same boundary discipline as the `expectedSetting` coercion below: the model-facing schema
    accepts any string so one bad value cannot fail the parse, but the review table should only
    ever be offered values the picker can hold — parseable local instants inside the event's own
    date range. Anything else degrades to an unfilled picker, which is what it would have been
    before spec 13 anyway."""
    starts, ends = event.get("startsOn"), event.get("endsOn")
    dropped = 0
    for stage in out.stages:
        for attr in ("proposedStartLocal", "proposedEndLocal"):
            raw = (getattr(stage, attr) or "").strip()
            if not raw:
                continue
            try:
                instant = dt.datetime.fromisoformat(raw)
            except ValueError:
                instant = None
            day = instant.date().isoformat() if instant else ""
            if instant is None or not (starts and ends and str(starts) <= day <= str(ends)):
                setattr(stage, attr, "")
                dropped += 1
                continue
            setattr(stage, attr, instant.strftime("%Y-%m-%dT%H:%M"))
    if dropped:
        log.warn("itinerary_proposed_instants_dropped", event_id=event_id, count=dropped)


def _itinerary_file(req: ParseItineraryRequest) -> tuple[bytes, str] | None:
    """Decode and bound the optional PDF/screenshot. 400s are specific: a host mid-wizard needs to
    know whether the file was too big, the wrong kind, or not base64 — not "parse failed"."""
    if not req.fileBase64:
        return None
    mime = (req.fileMime or "").strip().lower()
    if mime not in ITINERARY_FILE_MIMES:
        raise errors.bad_request(
            "FILE_TYPE", f"fileMime must be one of {', '.join(ITINERARY_FILE_MIMES)}"
        )
    try:
        data = base64.b64decode(req.fileBase64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise errors.bad_request("FILE_ENCODING", "fileBase64 is not valid base64") from exc
    limit = ITINERARY_PDF_MAX_BYTES if mime == "application/pdf" else ITINERARY_IMAGE_MAX_BYTES
    if len(data) > limit:
        raise errors.ApiError(
            413, "FILE_TOO_LARGE", f"the itinerary file is over {limit // (1024 * 1024)} MB"
        )
    if not data:
        raise errors.bad_request("FILE_EMPTY", "the itinerary file decoded to zero bytes")
    return data, mime


def _date_range_part(event: dict[str, Any]) -> str:
    """The event's own calendar, as prompt context — what licenses `proposedStartLocal` at all."""
    starts, ends, tz = event.get("startsOn"), event.get("endsOn"), event.get("timezone")
    if starts and ends:
        return (
            f"EVENT DATE RANGE: {starts} to {ends} (inclusive), timezone {tz}. Anchor proposed "
            f"instants to these dates per your instructions; never propose outside this range."
        )
    return (
        "EVENT DATE RANGE: not set. Leave every proposedStartLocal/proposedEndLocal empty; "
        "extract timeHint text only."
    )


@router.post("/itinerary/parse")
async def parse_itinerary(
    req: ParseItineraryRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> ItineraryParseOut:
    """Model Armor sanitize → Gemini structured parse → an editable, **not yet saved** proposal
    (spec 08 §3.2, extended by spec 13 to PDF/screenshot input and date-anchored proposals).
    Saving happens through `PUT /stages`, once the host has reviewed/fixed it — "an LLM parse of a
    WhatsApp itinerary forward is never silently authoritative."

    Armor runs twice, on the two texts that exist at two different times: the paste before the
    model sees it, and `sourceText` — the schedule the model transcribed out of a file — after,
    because Armor is text-only (§4.7) and cannot screen bytes. A block on either rejects the whole
    proposal; nothing from a rejected parse reaches the review table.
    """
    _require_host(principal, eventId)
    event = _event_or_404(eventId)

    file_part = _itinerary_file(req)
    text = (req.rawText or "").strip()
    if not text and file_part is None:
        raise errors.bad_request("EMPTY", "paste an itinerary or attach a PDF/screenshot")

    if text:
        try:
            armor.guard(text, surface="itinerary_paste", event_id=eventId)
        except armor.ArmorBlocked as exc:
            raise errors.bad_request(
                "TEXT_REJECTED", f"this paste looked like {', '.join(exc.filters) or 'a policy match'}"
            ) from exc

    parts: list[types.Part] = [gemini.as_text_part(_date_range_part(event))]
    if text:
        parts.append(gemini.as_text_part(text))
    if file_part is not None:
        # Inline bytes, full default resolution — deliberately NOT `MEDIA_RESOLUTION_LOW`, the
        # opposite call from the B2-S4 finding the perception workers follow: LOW is calibrated
        # for "what is in this photo" on an 8/s queue, and this is "read the small text in this
        # screenshot" once per wizard. The ~1k-token difference is noise at this call rate.
        parts.append(gemini.as_image_part(file_part[0], file_part[1]))

    try:
        out, usage = await gemini.run_structured(
            _itinerary_agent(),
            parts,
            ItineraryParseOut,
            stage="itinerary_parse",
        )
    except gemini.PermanentModelError as exc:
        raise errors.bad_request(
            "PARSE_FAILED", "couldn't make sense of that itinerary — try pasting it as plain text"
        ) from exc
    except gemini.ModelError as exc:
        raise errors.ApiError(503, "MODEL_UNAVAILABLE", str(exc)) from exc

    if file_part is not None:
        # The post-parse half of the Armor contract in the docstring. `sourceText` plus the labels
        # themselves — belt and braces for a model that ignored the transcription instruction.
        derived = " ".join(
            [out.sourceText or ""]
            + [f"{s.label} {s.timeHint}" for s in out.stages]
        ).strip()
        if derived:
            try:
                armor.guard(derived, surface="itinerary_file", event_id=eventId)
            except armor.ArmorBlocked as exc:
                raise errors.bad_request(
                    "TEXT_REJECTED",
                    f"the file's contents looked like {', '.join(exc.filters) or 'a policy match'}",
                ) from exc

    _coerce_proposed_instants(out, event, eventId)

    # `expectedSetting` is coerced here rather than left for the console. The model-facing schema
    # accepts any string on purpose (an unrecognised value must not fail the whole parse and burn the
    # single retry), but `EventStage.expectedSetting` is a strict enum — so a hallucinated "garden_area"
    # would sail through this endpoint and then 422 on `PUT /stages`, surfacing to the host as "saving
    # your timeline failed" with no clue why. Coerce at the boundary where the untrusted value stops
    # being untrusted: the host's review table should only ever be offered values it can save.
    dropped = 0
    for stage in out.stages:
        candidate = (stage.expectedSetting or "").strip().lower()
        if not candidate:
            continue
        try:
            stage.expectedSetting = SceneSetting(candidate).value
        except ValueError:
            stage.expectedSetting = ""
            dropped += 1
    if dropped:
        log.warn("itinerary_setting_dropped", event_id=eventId, count=dropped)

    # Not folded into `event.costSoFarUsd`: that field is the per-media perception pipeline's
    # running total (spec 10 §2), and this is a one-off host-side call on a different cost path —
    # inventing a second accumulation mechanism here risks disagreeing with whichever one the
    # Story Director's own tick cost already uses. Logged instead; the spend is a fraction of a cent.
    log.info(
        "itinerary_parsed",
        event_id=eventId,
        stages=len(out.stages),
        warnings=len(out.warnings),
        settings_kept=sum(1 for s in out.stages if s.expectedSetting),
        tokens_in=usage.tokensIn,
        tokens_out=usage.tokensOut,
    )
    return out


@router.put("/stages")
async def save_stages(
    req: SaveStagesRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """The host's reviewed/edited stage table, committed (spec 08 §3.2/§6). Callable any time
    before Go Live to fix a mistake, and after — a host correcting a stage window mid-event is a
    real, supported edit, not just a wizard step; `activeStage`/`stageOverride` are untouched.

    Two spec-13 additions, both at this writer because it is the only writer:

    - **Armor on the labels.** Stage labels and moment labels ride into per-photo prompts
      (`workers/curate/agent.py::event_context` prints both), and `update_profile` above already
      established the rule: "checked at onboarding" has to be literally true at every endpoint
      that can put host free text on that path. This one could not say that until now — and with
      PDF/screenshot parses feeding this table, it is also the choke point that covers every input
      modality at once.
    - **Chronological order.** The array is sorted by `startsAt` (undated stages last, otherwise
      stable), so array order and time order are the same thing everywhere downstream — the review
      table, the gallery chips, and `publisher/store.py`'s previous-stage fallback for undated
      stages all trust it.
    """
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    if event.get("status") == EventStatus.WRAPPED.value:
        raise errors.conflict("EVENT_WRAPPED", "this event has wrapped — its timeline is read-only")

    guarded = " ".join(
        [s.label for s in req.stages]
        + [m.label for s in req.stages for m in (s.requiredMoments or [])]
    )
    if guarded.strip():
        try:
            armor.guard(guarded, surface="stage_table", event_id=eventId)
        except armor.ArmorBlocked as exc:
            raise errors.bad_request(
                "TEXT_REJECTED",
                f"a stage or moment label looked like {', '.join(exc.filters) or 'a policy match'}",
            ) from exc

    ordered = sorted(
        req.stages,
        key=lambda s: (s.startsAt is None, s.startsAt or dt.datetime.max.replace(tzinfo=dt.timezone.utc)),
    )
    # NOT mode="json": `startsAt`/`endsAt` must stay real datetimes, or the stage-fusion temporal
    # prior (spec 03 §5.1) can never compare them against a photo's `capturedAt` again.
    payload = [fs.to_firestore(s.model_dump(by_alias=True)) for s in ordered]
    fs.event_ref(eventId).update({"stages": payload})
    log.info("stages_saved", event_id=eventId, count=len(payload))
    return {"stages": payload}


@router.post("/details")
async def update_details(
    req: DetailsUpdateRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Correct the event's name, date range or expected head-count after creation (spec 13) — a
    trip that slips a day is a real edit, same posture as `save_stages` above. Absent fields are
    untouched; an explicit `null` clears (dates clear only as a pair). Day indices are derived
    everywhere (`shared/eventtime.py`), so a corrected start date is simply correct on the next
    read — nothing stored goes stale."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    if event.get("status") == EventStatus.WRAPPED.value:
        raise errors.conflict("EVENT_WRAPPED", "this event has wrapped — its details are read-only")

    sent = req.model_fields_set
    updates: dict[str, Any] = {}
    if "name" in sent and req.name:
        updates["name"] = req.name
    if "startDate" in sent or "endDate" in sent:
        starts = req.startDate if "startDate" in sent else event.get("startsOn")
        ends = req.endDate if "endDate" in sent else event.get("endsOn")
        if (starts is None) != (ends is None):
            raise errors.bad_request("DATE_RANGE", "a dated event needs both a start and an end date")
        if starts and ends and ends < starts:
            raise errors.bad_request("DATE_RANGE", "the end date is before the start date")
        updates["startsOn"], updates["endsOn"] = starts, ends
    if "expectedParticipants" in sent:
        updates["expectedParticipants"] = req.expectedParticipants
    if not updates:
        raise errors.bad_request("EMPTY", "nothing to update")

    fs.event_ref(eventId).update(updates)
    log.info("details_updated", event_id=eventId, fields=",".join(sorted(updates)))
    return updates


# ==================================================================================== lifecycle (spec 08 §2)


def _contact_url() -> str:
    return "mailto:hello@showrunner.dev?subject=Showrunner%20capacity"


@firestore.transactional
def _go_live_txn(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    kill_ref: firestore.DocumentReference,
    counter_ref: firestore.DocumentReference,
) -> tuple[str, dict[str, Any] | None, dt.datetime | None]:
    """Returns `(outcome, detail, liveAt)`. Never raises an `ApiError` from inside a
    `@firestore.transactional` callable — this codebase's convention (`shared/leases.py`) is a
    plain result the caller translates to HTTP, since an application exception racing the
    library's own contention-retry is not a combination worth relying on."""
    snap = event_ref.get(transaction=transaction)
    if not snap.exists:
        return "NO_EVENT", None, None
    event = snap.to_dict() or {}
    if event.get("status") != EventStatus.DRAFT.value:
        return "NOT_DRAFT", event, None
    if not event.get("stages"):
        return "NO_STAGES", event, None

    is_public = event.get("class", EventClass.PUBLIC.value) == EventClass.PUBLIC.value
    if is_public:
        kill_snap = kill_ref.get(transaction=transaction)
        if not (kill_snap.to_dict() or {}).get("enabled", True):
            return "PUBLIC_CREATION_DISABLED", event, None
        counter_snap = counter_ref.get(transaction=transaction)
        count = int((counter_snap.to_dict() or {}).get("count") or 0)
        if count >= settings().max_concurrent_live_events:
            return "CAPACITY", event, None
        transaction.set(counter_ref, {"count": count + 1}, merge=True)

    now = dt.datetime.now(dt.timezone.utc)
    transaction.update(event_ref, {"status": EventStatus.LIVE.value, "liveAt": now})
    return "OK", event, now


@router.post("/lifecycle/go-live", response_model=LifecycleResponse)
async def go_live(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> LifecycleResponse:
    """The capacity-gated `draft → live` transition (spec 11 §1.2/§6, verbatim path)."""
    _require_host(principal, eventId)
    outcome, event, live_at = _go_live_txn(
        fs.db().transaction(),
        fs.event_ref(eventId),
        fs.platform_doc("publicCreationEnabled"),
        fs.platform_doc("liveEventCount"),
    )
    if outcome == "NO_EVENT":
        raise errors.not_found("NO_EVENT", "unknown event")
    if outcome == "NOT_DRAFT":
        raise errors.conflict("NOT_DRAFT", f"event is {event.get('status') if event else '?'}, not draft")
    if outcome == "NO_STAGES":
        raise errors.bad_request("NO_STAGES", "review and save at least one stage before going live")
    if outcome == "PUBLIC_CREATION_DISABLED":
        raise errors.forbidden("PUBLIC_CREATION_DISABLED", "public events are paused right now")
    if outcome == "CAPACITY":
        raise errors.ApiError(
            409,
            "CAPACITY",
            f"{settings().max_concurrent_live_events} events are already live — contact the developer",
            contactUrl=_contact_url(),
        )
    log.info("event_live", event_id=eventId, by=principal.uid)
    _nudge_publisher(eventId, "go_live")
    return LifecycleResponse(eventId=eventId, status=EventStatus.LIVE.value, liveAt=live_at)


def _guarded_transition(
    event_id: str, *, expect: EventStatus, to: EventStatus, extra: dict[str, Any] | None = None
) -> dt.datetime:
    """Read-check-write for the transitions that touch no counter (spec 08 §2's simpler edges).
    Not a transaction: the only writer of `status` besides this module is the director tick reading
    it (never writing it back), so the race this would protect against does not exist today."""
    event = _event_or_404(event_id)
    if event.get("status") != expect.value:
        raise errors.conflict("BAD_TRANSITION", f"event is {event.get('status')}, expected {expect.value}")
    now = dt.datetime.now(dt.timezone.utc)
    fs.event_ref(event_id).update({"status": to.value, **(extra or {})})
    return now


@router.post("/lifecycle/pause", response_model=LifecycleResponse)
async def pause_event(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> LifecycleResponse:
    _require_host(principal, eventId)
    _guarded_transition(eventId, expect=EventStatus.LIVE, to=EventStatus.PAUSED)
    log.info("event_paused", event_id=eventId, by=principal.uid)
    return LifecycleResponse(eventId=eventId, status=EventStatus.PAUSED.value)


@router.post("/lifecycle/resume", response_model=LifecycleResponse)
async def resume_event(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> LifecycleResponse:
    _require_host(principal, eventId)
    _guarded_transition(eventId, expect=EventStatus.PAUSED, to=EventStatus.LIVE)
    log.info("event_resumed", event_id=eventId, by=principal.uid)
    return LifecycleResponse(eventId=eventId, status=EventStatus.LIVE.value)


@router.post("/lifecycle/wrap", response_model=LifecycleResponse)
async def wrap_event(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> LifecycleResponse:
    """`live|paused → wrapping` (spec 08 §2 step 1). The 30-minute upload grace window and the
    autonomous final tick are the Story Director's job whenever one is wired into the tick (spec
    05 §1 ticks `wrapping` events too); `POST /lifecycle/finalize` is always the host's own way to
    close out an event and get an honest report, autonomous finale or not — a host must never be
    stuck in `wrapping` waiting on a background agent that didn't run."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    if event.get("status") not in (EventStatus.LIVE.value, EventStatus.PAUSED.value):
        raise errors.conflict("BAD_TRANSITION", f"event is {event.get('status')}, expected live/paused")
    fs.event_ref(eventId).update({"status": EventStatus.WRAPPING.value})
    log.info("event_wrapping", event_id=eventId, by=principal.uid)
    _nudge_publisher(eventId, "wrap")
    return LifecycleResponse(eventId=eventId, status=EventStatus.WRAPPING.value)


@firestore.transactional
def _finalize_txn(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    counter_ref: firestore.DocumentReference,
) -> tuple[str, dict[str, Any] | None, dt.datetime | None]:
    snap = event_ref.get(transaction=transaction)
    if not snap.exists:
        return "NO_EVENT", None, None
    event = snap.to_dict() or {}
    if event.get("status") != EventStatus.WRAPPING.value:
        return "NOT_WRAPPING", event, None

    if event.get("class", EventClass.PUBLIC.value) == EventClass.PUBLIC.value:
        counter_snap = counter_ref.get(transaction=transaction)
        count = int((counter_snap.to_dict() or {}).get("count") or 0)
        transaction.set(counter_ref, {"count": max(0, count - 1)}, merge=True)

    now = dt.datetime.now(dt.timezone.utc)
    transaction.update(event_ref, {"status": EventStatus.WRAPPED.value, "wrappedAt": now})
    return "OK", event, now


def _honest_gaps(event: dict[str, Any], shards: dict[str, coverage.StageCoverage]) -> list[StageGap]:
    gaps: list[StageGap] = []
    for stage in event.get("stages") or []:
        stage_id = str(stage.get("stageId") or "")
        shard = shards.get(stage_id)
        seen = shard.moments if shard else {}
        for moment in stage.get("requiredMoments") or []:
            moment_id = str(moment.get("momentId") or "")
            if moment_id and seen.get(moment_id, 0) == 0:
                gaps.append(
                    StageGap(
                        stageId=stage_id,
                        stageLabel=str(stage.get("label") or stage_id),
                        momentId=moment_id,
                        momentLabel=str(moment.get("label") or moment_id),
                    )
                )
    return gaps


def _merge_permanent_gaps(event_id: str, event: dict[str, Any], gaps: list[StageGap]) -> list[StageGap]:
    """Fold `directorState.permanentGaps` into the honest-gaps list (spec 13 §8) — spec 05 §3's
    original promise, finally kept: the report says not only what is missing but that the system
    *asked* (an expired bounty) or watched the window close (a lapsed under-covered stage).
    De-duplicated on (stageId, momentId); the director's record only ever adds the `detail`."""
    labels = {
        str(s.get("stageId")): str(s.get("label") or s.get("stageId"))
        for s in (event.get("stages") or [])
    }
    seen = {(g.stageId, g.momentId) for g in gaps}
    by_key = {(g.stageId, g.momentId): g for g in gaps}
    try:
        state = fs.director_state_ref(event_id).get().to_dict() or {}
    except Exception as exc:  # noqa: BLE001 - the report must render even if this read hiccups
        log.warn("wrap_permanent_gaps_read_failed", event_id=event_id, err=str(exc))
        return gaps
    for record in state.get("permanentGaps") or []:
        if not isinstance(record, dict):
            continue
        stage_id = str(record.get("targetStage") or "")
        moment_id = str(record.get("targetMoment") or "")
        if not stage_id or not moment_id:
            continue
        detail = (
            "the stage ended with this still under-covered"
            if record.get("kind") == "lapsed_stage"
            else "a bounty asked for this and expired unfilled"
        )
        key = (stage_id, moment_id)
        if key in seen:
            existing = by_key[key]
            if not existing.detail:
                existing.detail = detail
            continue
        gap = StageGap(
            stageId=stage_id,
            stageLabel=labels.get(stage_id, stage_id),
            momentId=moment_id,
            momentLabel=str(record.get("title") or moment_id),
            detail=detail,
        )
        gaps.append(gap)
        seen.add(key)
        by_key[key] = gap
    return gaps


def _best_media_per_stage(event_id: str, per_stage: int = 3) -> dict[str, list[str]]:
    """Top mediaIds per stage by the Curator's stored aesthetic (spec 13 §8) — the reel selector's
    existing composite index (`visibility, status, curator.aestheticScore desc`), grouped in
    Python. `pool` included deliberately: the wrap panel is host-authed, and coverage counts
    evidence, not exposure."""
    best: dict[str, list[str]] = {}
    try:
        query = (
            fs.media_col(event_id)
            .where(filter=firestore.FieldFilter("visibility", "in", ["public", "pool"]))
            .where(filter=firestore.FieldFilter("status", "==", "indexed"))
            .order_by("curator.aestheticScore", direction=firestore.Query.DESCENDING)
            .limit(300)
        )
        for snap in query.stream():
            doc = snap.to_dict() or {}
            if doc.get("deleted") or doc.get("duplicateOf"):
                continue
            stage_id = str((doc.get("curator") or {}).get("stageId") or "")
            if not stage_id:
                continue
            bucket = best.setdefault(stage_id, [])
            if len(bucket) < per_stage:
                bucket.append(snap.id)
    except Exception as exc:  # noqa: BLE001 - best-of thumbnails are decoration on the report
        log.warn("wrap_best_media_failed", event_id=event_id, err=str(exc))
    return best


def _recap_reel_id(event_id: str) -> str | None:
    """The wrap-commissioned `event_recap`'s reelId from the director's commission record."""
    try:
        state = fs.director_state_ref(event_id).get().to_dict() or {}
        for record in reversed(state.get("commissions") or []):
            if isinstance(record, dict) and record.get("persona") == "event_recap" and record.get("reelId"):
                return str(record["reelId"])
    except Exception as exc:  # noqa: BLE001
        log.warn("wrap_recap_lookup_failed", event_id=event_id, err=str(exc))
    return None


def _top_contributors(event_id: str, limit: int = 5) -> list[Contributor]:
    query = fs.guests_col(event_id).order_by(
        "points", direction=firestore.Query.DESCENDING
    ).limit(limit)
    people_names: dict[str, str | None] = {}
    out: list[Contributor] = []
    for snap in query.stream():
        guest = snap.to_dict() or {}
        points = int(guest.get("points") or 0)
        if points <= 0:
            continue
        person_id = guest.get("personId")
        name = None
        if person_id:
            if person_id not in people_names:
                person = fs.person_ref(event_id, str(person_id)).get().to_dict() or {}
                people_names[person_id] = person.get("displayName")
            name = people_names[person_id]
        out.append(Contributor(uid=snap.id, displayName=name, points=points))
    return out


async def _headline(event: dict[str, Any], stats: dict[str, Any], gaps: list[StageGap]) -> str:
    """Best-effort — a wrap must never block on a model call (module docstring, `schemas/wrap_out.py`)."""
    fallback = (
        f"{event.get('name') or 'The event'} wrapped — "
        f"{stats['totalPhotos']} photos across {len(stats['perStage'])} stage(s)."
    )
    facts = [f"{stats['totalPhotos']} photos total", f"{stats['totalPhotographers']} photographers"]
    for row in stats["perStage"]:
        facts.append(f"{row.label}: {row.photoCount} photos, {row.highlightCount} highlights")
    for gap in gaps:
        facts.append(f"no photos of {gap.momentLabel} during {gap.stageLabel}")
    # The Event Diary's chapter memos (spec 13 §8) — the one input here that is texture rather
    # than a number, which is exactly what a headline needs to sound like the event it is about.
    for stage_id, memo in sorted(diary_mod.recall_all(str(event.get("eventId") or "")).items()):
        facts.append(f"how {stage_id} felt: {memo}")
    try:
        agent = LlmAgent(
            name="wrap_writer",
            description="Writes one honest headline sentence for an event's wrap-up report.",
            model=gemini.adk_model(settings().model_director),
            instruction=(
                "Write exactly one sentence (max 160 chars) summarising this event's photo "
                "coverage for the host and the kiosk finale slide. Use only the facts given — "
                "never invent a number, a name or a gap that isn't listed. Warm, not corporate."
            ),
            output_schema=WrapHeadlineOut,
            output_key="wrap",
        )
        out, _usage = await gemini.run_structured(
            agent, [gemini.as_text_part("\n".join(facts))], WrapHeadlineOut, stage="wrap_headline"
        )
        return out.headline or fallback
    except gemini.ModelError as exc:
        log.warn("wrap_headline_failed", event_id=event.get("eventId"), err=str(exc))
        return fallback


@router.post("/lifecycle/finalize", response_model=WrapReport)
async def finalize_event(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> WrapReport:
    """`wrapping → wrapped` + the wrap-up report (spec 08 §2 steps 3-4). The capacity slot frees in
    the same transaction as the status flip (spec 11 §1.2) — this is that transaction."""
    _require_host(principal, eventId)
    outcome, event, wrapped_at = _finalize_txn(
        fs.db().transaction(), fs.event_ref(eventId), fs.platform_doc("liveEventCount")
    )
    if outcome == "NO_EVENT":
        raise errors.not_found("NO_EVENT", "unknown event")
    if outcome == "NOT_WRAPPING":
        raise errors.conflict("BAD_TRANSITION", f"event is {event.get('status') if event else '?'}, expected wrapping")
    if event is None or wrapped_at is None:  # unreachable except "OK", kept for the type-checker
        raise errors.ApiError(500, "INTERNAL", "finalize produced no result")

    shards = coverage.read(eventId)
    calendar = EventCalendar.of(event)
    best_media = _best_media_per_stage(eventId)
    per_stage = [
        StageReportRow(
            stageId=str(stage.get("stageId") or ""),
            label=str(stage.get("label") or stage.get("stageId") or ""),
            photoCount=(shards.get(str(stage.get("stageId"))) or coverage.StageCoverage(stage_id="")).photo_count,
            highlightCount=(shards.get(str(stage.get("stageId"))) or coverage.StageCoverage(stage_id="")).highlight_count,
            meanAesthetic=round(
                (shards.get(str(stage.get("stageId"))) or coverage.StageCoverage(stage_id="")).mean_aesthetic, 3
            ),
            dayLabel=(
                calendar.day_label(starts) if (starts := stages_lib.as_dt(stage.get("startsAt"))) else ""
            ),
            bestMediaIds=best_media.get(str(stage.get("stageId")), []),
        )
        for stage in event.get("stages") or []
    ]
    gaps = _merge_permanent_gaps(eventId, event, _honest_gaps(event, shards))
    total_photos = _count(fs.media_col(eventId))
    total_reels = _count(fs.reels_col(eventId))
    total_photographers = _count(
        fs.guests_col(eventId).where(filter=firestore.FieldFilter("uploads", ">", 0))
    )
    contributors = _top_contributors(eventId)
    stats = {
        "totalPhotos": total_photos,
        "totalPhotographers": total_photographers,
        "perStage": per_stage,
    }
    headline = await _headline(event, stats, gaps)

    report = WrapReport(
        eventId=eventId,
        generatedAt=wrapped_at,
        headline=headline,
        totalPhotos=total_photos,
        totalReels=total_reels,
        totalPhotographers=total_photographers,
        perStage=per_stage,
        honestGaps=gaps,
        topContributors=contributors,
        recapReelId=_recap_reel_id(eventId),
    )
    fs.event_ref(eventId).update({"wrapReport": fs.to_firestore(report.model_dump(by_alias=True))})
    log.info(
        "event_wrapped",
        event_id=eventId,
        by=principal.uid,
        photos=total_photos,
        gaps=len(gaps),
    )
    _nudge_publisher(eventId, "wrapped")
    return report


@router.get("/wrap-report", response_model=WrapReport)
async def get_wrap_report(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> WrapReport:
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    stored = event.get("wrapReport")
    if not stored:
        raise errors.not_found("NO_REPORT", "this event hasn't wrapped yet")
    return WrapReport.model_validate(stored)


# ==================================================================================== stage override (spec 05 §2)


@router.post("/stage-override")
async def set_stage_override(
    req: StageOverrideRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """"Now: ▶ stage" — always wins over the schedule/evidence fusion, instantly (spec 05 §2).
    `stageId: null` clears the override and returns control to the timeline."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)
    if req.stageId is not None:
        ids = {str(s.get("stageId")) for s in event.get("stages") or []}
        if req.stageId not in ids:
            raise errors.bad_request("UNKNOWN_STAGE", f"'{req.stageId}' is not one of this event's stages")
    fs.event_ref(eventId).update({"stageOverride": req.stageId})
    log.info("stage_override", event_id=eventId, stage=req.stageId, by=principal.uid)
    _nudge_publisher(eventId, "stage_override")
    return {"stageOverride": req.stageId}


# ==================================================================================== panic (spec 08 §5)


@router.post("/freeze")
async def set_freeze(
    req: FreezeRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """One write, ≤2s effect (spec 08 §5's acceptance). Existing public items keep their stored
    `visibility` — a freeze suspends the *surfaces*, never rewrites the trust decision underneath
    them, so unfreeze is instant and exact (`backend/publisher/runner.py`, `shared/visibility.py`)."""
    _require_host(principal, eventId)
    _event_or_404(eventId)
    fs.event_ref(eventId).update({"publicFrozen": req.frozen})
    fs.ops_alert(
        eventId,
        "public_frozen" if req.frozen else "public_unfrozen",
        f"host {'froze' if req.frozen else 'unfroze'} the public surfaces",
        severity="info",
        resolved=True,
        by=principal.uid,
    )
    log.info("public_frozen", event_id=eventId, frozen=req.frozen, by=principal.uid)
    _nudge_publisher(eventId, "freeze" if req.frozen else "unfreeze")
    return {"publicFrozen": req.frozen}


# ==================================================================================== console summary


@router.get("/console", response_model=ConsoleSummary)
async def console_summary(
    eventId: str = Path(min_length=1, max_length=128), principal: Principal = Depends(caller)
) -> ConsoleSummary:
    """The KPI header row (spec 12 §8) — real aggregates only, never a placeholder. The coverage
    heat-grid itself is cut this session; `coveragePct` folds it into the one number the header
    actually needs (share of this event's required moments with at least one photo)."""
    _require_host(principal, eventId)
    event = _event_or_404(eventId)

    photos = _count(fs.media_col(eventId))
    guests = _count(fs.guests_col(eventId))

    shards = coverage.read(eventId)
    required: list[tuple[str, str]] = [
        (str(stage.get("stageId") or ""), str(m.get("momentId") or ""))
        for stage in event.get("stages") or []
        for m in stage.get("requiredMoments") or []
    ]
    if required:
        covered = sum(
            1
            for stage_id, moment_id in required
            if (shards.get(stage_id) or coverage.StageCoverage(stage_id="")).moments.get(moment_id, 0) > 0
        )
        coverage_pct = round(100.0 * covered / len(required), 1)
    else:
        coverage_pct = 0.0

    live_count = None
    if event.get("class", EventClass.PUBLIC.value) == EventClass.PUBLIC.value:
        live_count = int((fs.platform_doc("liveEventCount").get().to_dict() or {}).get("count") or 0)

    return ConsoleSummary(
        eventId=eventId,
        status=str(event.get("status") or ""),
        photos=photos,
        guests=guests,
        coveragePct=coverage_pct,
        # Derived from the per-media token counters every worker already writes, not read off the
        # event document. `event.costSoFarUsd` is a schema field that nothing has ever incremented, so
        # reading it here showed "$0.00" for the life of every event — a money number that is
        # confidently wrong reads as "this event is free". See `shared/spend.py` for why it is summed
        # server-side rather than incremented (spec 09 §2's 8/s queues would make the event document a
        # hot key). Falls back to the stored field if some future writer starts maintaining it.
        costSoFarUsd=round(spend.usd(eventId) or float(event.get("costSoFarUsd") or 0.0), 2),
        publicFrozen=bool(event.get("publicFrozen")),
        liveEventCount=live_count,
        reviewCount=moderation.pending_review_count(eventId, GuardianVerdict.HOST_REVIEW),
        blockedCount=moderation.pending_review_count(eventId, GuardianVerdict.BLOCKED),
    )
