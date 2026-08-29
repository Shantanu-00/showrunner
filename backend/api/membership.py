"""`POST /v1/events/{eventId}/join` — the door, and the only way to become an event member.

**Why this exists.** Until now `firestore.rules`'s `isMember()` was literally `signedIn()`: it took
no eventId, because anonymous sign-in *is* the act of arriving at an event (spec 02 §1) and there was
no membership document to consult. The consequence, flagged rather than hidden in HANDOFF §9's "S9
choices that no spec pinned" (item d), is that an anonymous uid holding **any** eventId could read
that event's public media, `people` names and tiers, `guests` leaderboard, `bounties` and published
`reels`. Every event was a public event protected only by a 128-bit link. This endpoint plus
`isMember(eventId)` is what closes it.

**Why a claim and not a document.** Rules allow ten `get()`s per request and bill each one, so a
membership *document* checked from a rule would cap a gallery grid at ten photos
(`firestore.rules`'s own header, property 1). Membership therefore rides in a custom claim —
`members: [eventId, …]` — exactly like `personId` and `hosts`, minted server-side on the caller's own
uid and never self-asserted. `shared/auth.py::grant_event_claim` appends through the read-merge-write
helper, so joining an event cannot erase an enrolled guest's `personId`.

**Why it writes `guests/{uid}`.** That document already existed; `api/uploads.py::_register_batch`
creates it lazily inside the upload transaction with `merge=True`. This endpoint creates the same
document, with the same merge discipline, before the first upload — so the two paths reconcile rather
than compete: whichever runs first creates it, the other merges into it, and neither ever overwrites
the other's fields (`points`, `uploads`, the rate-limit window, `banned`).

**Idempotency.** Rejoining the same uid must not double-count a seat, and a PWA calls this on every
page load. The seat counter is therefore incremented in the **same transaction** as the
`guests/{uid}` create, and only when that document did not already exist — the pattern
`host.py::_go_live_txn` uses for `platform/liveEventCount`. The claim grant is separately idempotent
(it skips the write when the eventId is already in the array), so a double-tap costs nothing.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, Depends, Path
from google.cloud import firestore

from schemas.event import EventAccessMode, EventStatus
from schemas.membership import JoinCodeRequest, JoinCodeResponse, JoinRequest, JoinResponse
from shared import errors, fs, log
from shared.auth import Principal, TooManyEventClaims, caller, grant_event_claim
from shared.settings import CODE_LOOKUP_RATE_LIMIT_PER_HOUR

# The invite code is sha256-hashed by the same one-liner that hashes host links, recovery codes and
# album claim links — deliberately imported rather than re-implemented, so "how does this codebase
# store a redeemable code" has exactly one answer. `host.py` imports nothing from here, so there is
# no cycle.
from .host import _code_hash

router = APIRouter(prefix="/v1/events/{eventId}", tags=["membership"])

#: Statuses in which the door is open at all. A `wrapped` event is a read-only archive — the people
#: who were there keep the membership claim they already hold, and nobody new is admitted to it,
#: which is the same reasoning `UPLOAD_OPEN_STATUSES` applies to bytes. `draft` is open because the
#: host tests their own event before Go Live and does so from an ordinary guest session.
JOINABLE_STATUSES = frozenset(
    {
        EventStatus.DRAFT.value,
        EventStatus.LIVE.value,
        EventStatus.PAUSED.value,
        EventStatus.WRAPPING.value,
    }
)


# ---------------------------------------------------------------- the mode, on the byte-serving path

#: How long `access_mode` may serve a cached answer. `api/media.py` and `api/reels.py` consult the
#: mode on **every** render request, which is the hottest read in the system (one per `<img>` on a
#: kiosk slideshow), and the mode is a field on the event document — so without this, closing the
#: door would double the Firestore reads on the photo path forever.
#:
#: Five seconds, and the direction of the staleness is the reason for the number. Flipping
#: `open → invite` is the *tightening* direction, so a stale cache keeps serving unauthenticated
#: bytes for up to five seconds after the host shuts the door. That is a bounded, stated window, not
#: an open one, and it is deliberately not the panic control: `POST /freeze` (spec 08 §5, ≤2 s) reads
#: no cache and is what a host reaches for when something is on the wall that must come off now.
_MODE_TTL_SECONDS = 5.0
_mode_cache: dict[str, tuple[float, str]] = {}
#: A long-lived `api` instance sees a growing, never-shrinking set of eventIds otherwise — nothing
#: ever pops an entry, only overwrites one. Not a real problem at hackathon scale (thousands of
#: events, a few bytes each), but cheap to bound: a sweep evicts anything past its TTL whenever the
#: cache grows past this size, so it self-limits instead of trusting demo-length uptime.
_MODE_CACHE_MAX_ENTRIES = 2048


def access_mode(event_id: str) -> str:
    """`'open'` or `'invite'` for this event, cached for `_MODE_TTL_SECONDS`.

    Defaults to `'open'` on a missing event or a missing field: every event created before `access`
    existed is an open event, and an exception here would take down the photo path for a field that
    is absent on most documents in the database.
    """
    now = time.monotonic()
    hit = _mode_cache.get(event_id)
    if hit and now - hit[0] < _MODE_TTL_SECONDS:
        return hit[1]
    event = fs.get_event(event_id) or {}
    mode = str((event.get("access") or {}).get("mode") or EventAccessMode.OPEN.value)
    if mode not in (EventAccessMode.OPEN.value, EventAccessMode.INVITE.value):
        mode = EventAccessMode.OPEN.value
    if len(_mode_cache) >= _MODE_CACHE_MAX_ENTRIES:
        stale = [k for k, v in _mode_cache.items() if now - v[0] >= _MODE_TTL_SECONDS]
        for key in stale:
            _mode_cache.pop(key, None)
    _mode_cache[event_id] = (now, mode)
    return mode


def is_invite_only(event_id: str) -> bool:
    return access_mode(event_id) == EventAccessMode.INVITE.value


def require_member_if_invite(event: dict[str, Any], event_id: str, principal: Principal) -> None:
    """Gate a *write* path on membership when the event is invite-only.

    Takes the event document the caller already has rather than an eventId, so this costs no read —
    `api/uploads.py` has fetched it to check the master switch two lines earlier.

    Reading and writing needed different answers here. An open event's uploads stay open: an eventId in
    a QR code is the invitation (spec 02 §1), and demanding a join round trip before the first photo
    would put a failure between a guest raising their phone and the wall, for no gain — the guest is
    about to be a member anyway, and every other guard on that path (rate limit, ban flag, content-type
    allowlist, size cap) is unchanged and already runs per uid. On an invite-only event it is a real
    hole: without this, a stranger holding the eventId could inject photographs into a private event's
    pipeline, which is a write nobody consented to and one that costs the host real money in Gemini
    calls.
    """
    mode = str((event.get("access") or {}).get("mode") or EventAccessMode.OPEN.value)
    if mode == EventAccessMode.INVITE.value and not principal.is_member_of(event_id):
        raise errors.forbidden(
            "NOT_A_MEMBER", "this event is invite-only — join it before uploading to it"
        )


def _code_matches_event(access: dict[str, Any], code_hash: str) -> bool:
    stored = access.get("codeHash")
    return bool(stored) and str(stored) == code_hash


def _kiosk_link_grants(event_id: str, code_hash: str) -> bool:
    """A kiosk link (`host.py::create_kiosk_link`) is the second accepted code shape.

    Same hashed-code machinery, stored in `hostLinks/{hash}` with `grants: 'member'`, so a venue TV
    gets membership from a link the host can revoke and that expires on its own — without the host
    having to hand the human-facing invite code to a device left unattended in a function hall. It is
    checked here rather than at `POST /v1/host-claim`, which refuses anything that is not
    `grants: 'host'`.
    """
    snap = fs.host_link_ref(code_hash).get()
    if not snap.exists:
        return False
    link = snap.to_dict() or {}
    expires_at = link.get("expiresAt")
    return (
        str(link.get("grants") or "host") == "member"
        and str(link.get("eventId") or "") == event_id
        and not link.get("revoked")
        and isinstance(expires_at, dt.datetime)
        and dt.datetime.now(dt.timezone.utc) <= expires_at
    )


@firestore.transactional
def _join_txn(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    guest_ref: firestore.DocumentReference,
    principal: Principal,
) -> tuple[str, int, int | None]:
    """Returns `(outcome, guestCount, maxGuests)`.

    Never raises an `ApiError` from inside a `@firestore.transactional` callable — this codebase's
    convention (`shared/leases.py`, `host.py::_go_live_txn`): a plain result the caller translates to
    HTTP, because an application exception racing the library's own contention retry is not a
    combination worth relying on.

    The event document is read *inside* the transaction so the seat check and the increment cannot
    straddle a host raising the cap, and the guest document is read in the same transaction so two
    tabs opening at once cannot both count as a new seat.

    **`joinedAt`, not document existence, is what marks a seat as taken.** `api/uploads.py` creates
    `guests/{uid}` lazily inside the upload transaction and has done since spec 01 §3, so the document
    may already exist without its owner ever having come through this door — keying off existence
    would leave `guestCount` permanently short by however many guests uploaded first. Keying off
    `joinedAt` counts each uid exactly once whichever path created the document.
    """
    event_snap = event_ref.get(transaction=transaction)
    if not event_snap.exists:
        return "NO_EVENT", 0, None
    event = event_snap.to_dict() or {}
    access = dict(event.get("access") or {})
    mode = str(access.get("mode") or EventAccessMode.OPEN.value)
    max_guests = access.get("maxGuests")
    max_guests = int(max_guests) if isinstance(max_guests, (int, float)) else None
    count = int(event.get("guestCount") or 0)

    guest_snap = guest_ref.get(transaction=transaction)
    existing = guest_snap.to_dict() or {} if guest_snap.exists else {}
    seated = bool(existing.get("joinedAt"))
    if existing.get("banned"):
        # A ban is enforced on uploads at the API (spec 01 §3) and is deliberately not a rules
        # concept, but re-admitting a banned session through the front door would be absurd.
        return "BANNED", count, max_guests

    # The cap is an invite-only property: "open" means anyone who scans the QR joins (HANDOFF §9's
    # own framing), so a seat number left over from a previous invite phase must not quietly refuse
    # guests at an event the host has since opened.
    if (
        not seated
        and mode == EventAccessMode.INVITE.value
        and max_guests is not None
        and count >= max_guests
    ):
        return "FULL", count, max_guests

    payload: dict[str, Any] = {
        "uid": principal.uid,
        # Recorded only when known. A `None` here would overwrite a personId that
        # `api/identity.py` had already linked on an earlier enrollment, because this endpoint runs
        # again on every page load.
        **({"personId": principal.person_id} if principal.person_id else {}),
        "lastSeenAt": fs.SERVER_TIMESTAMP,
    }
    if not seated:
        payload["joinedAt"] = fs.SERVER_TIMESTAMP
    if not existing.get("createdAt"):
        payload["createdAt"] = fs.SERVER_TIMESTAMP
    transaction.set(guest_ref, payload, merge=True)
    if seated:
        return "ALREADY_MEMBER", count, max_guests
    transaction.update(event_ref, {"guestCount": count + 1})
    return "JOINED", count + 1, max_guests


@router.post("/join", response_model=JoinResponse)
async def join_event(
    req: JoinRequest | None = None,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> JoinResponse:
    """Become a member of this event. Idempotent, cheap to call on every page load.

    Order of operations, and it is deliberate:

    1. The event must exist and its door must be open (`JOINABLE_STATUSES`).
    2. On an invite-only event the code must match — either the event's own `access.codeHash` or a
       kiosk link's hash. Both are sha256 comparisons against a stored hash; no plaintext code is
       stored anywhere, which is why a lost code can only be rotated, never recovered.
    3. One transaction: read the event and the guest doc, enforce the seat cap, create the guest doc
       and increment `guestCount` together.
    4. Only then mint the claim. A claim without a guest document would be a member the host cannot
       see; a guest document without a claim is simply a retry away from working, so if these two must
       fail out of order, this is the order to fail in.
    """
    code = (req.code or "").strip() if req else ""
    event = fs.get_event(eventId)
    if not event:
        raise errors.not_found("NO_EVENT", "unknown event")
    status = str(event.get("status") or EventStatus.DRAFT.value)
    if status not in JOINABLE_STATUSES:
        raise errors.forbidden(
            "EVENT_CLOSED", f"this event is {status} — it is no longer accepting new guests", status=status
        )

    access = dict(event.get("access") or {})
    mode = str(access.get("mode") or EventAccessMode.OPEN.value)
    if mode == EventAccessMode.INVITE.value and not (
        principal.is_host_of(eventId) or principal.platform_admin
    ):
        if not code:
            raise errors.forbidden("CODE_REQUIRED", "this event is invite-only — a code is needed to join")
        code_hash = _code_hash(code)
        if not (_code_matches_event(access, code_hash) or _kiosk_link_grants(eventId, code_hash)):
            raise errors.forbidden("BAD_CODE", "that invite code is not valid for this event")

    outcome, guest_count, max_guests = _join_txn(
        fs.db().transaction(), fs.event_ref(eventId), fs.guest_ref(eventId, principal.uid), principal
    )
    if outcome == "NO_EVENT":
        raise errors.not_found("NO_EVENT", "unknown event")
    if outcome == "BANNED":
        raise errors.forbidden("GUEST_BANNED", "this device cannot join this event")
    if outcome == "FULL":
        # Named "seats", never "guests": the cap counts uids, and one person routinely holds several
        # (spec 02 §1). A host reading "40 of 40 guests" on a 25-person party would conclude the count
        # is broken; "seats" is the honest word, and the remedy is one tap on the console.
        raise errors.conflict(
            "EVENT_FULL",
            f"this event has filled all {max_guests} of its seats — ask the host to add more",
            seats=max_guests,
            seatsUsed=guest_count,
        )

    try:
        members = grant_event_claim(principal.uid, "members", eventId)
    except TooManyEventClaims as exc:
        raise errors.conflict("TOO_MANY_EVENTS", str(exc)) from exc

    log.info(
        "event_joined",
        event_id=eventId,
        uid=principal.uid,
        outcome=outcome,
        mode=mode,
        seats_used=guest_count,
    )
    return JoinResponse(
        eventId=eventId,
        joined=True,
        newMember=outcome == "JOINED",
        mode=EventAccessMode(mode),
        seats=max_guests,
        seatsUsed=guest_count,
        memberOf=list(members),
    )


# ==================================================================== resolving a bare invite code
#
# `/join` (the code-entry page) has a code and no eventId, which is the same shape
# `POST /v1/host-claim` already solves for host links: a redeemable secret has to be addressable
# without knowing what it points at. Host links get that for free because they are stored *as*
# `hostLinks/{hash}`. An invite code is not — `host.py` keeps it as `access.codeHash` on the event
# document, which is the right home (rotating it is one field write, and there is exactly one
# authority for "is this code valid for this event"). So resolution here is a single equality query
# over the `events` collection instead of a document get. That needs no index configuration:
# `firestore.indexes.json` disables single-field indexing only for `media.createdAt`, so the automatic
# single-field index on `access.codeHash` is present.

_join_code_router = APIRouter(prefix="/v1/events", tags=["membership"])


def _rate_limit_code_lookup(uid: str) -> None:
    """Hourly budget per uid, same read-then-write shape as `host.py::_rate_limit_create`.

    Not a transaction, and for the same stated reason: the failure mode of a race here is "one caller
    gets one extra lookup in a burst", which is not a property this endpoint promises.
    """
    ref = fs.code_lookup_limiter_ref(uid)
    now = dt.datetime.now(dt.timezone.utc)
    doc = ref.get().to_dict() or {}
    started = doc.get("windowStartedAt")
    count = int(doc.get("count") or 0)
    if not isinstance(started, dt.datetime) or (now - started) > dt.timedelta(hours=1):
        ref.set({"windowStartedAt": now, "count": 1})
        return
    if count >= CODE_LOOKUP_RATE_LIMIT_PER_HOUR:
        raise errors.rate_limited("too many invite codes tried recently — wait a few minutes")
    ref.set({"count": firestore.Increment(1)}, merge=True)


def _event_for_code(code_hash: str) -> tuple[str, dict[str, Any]] | None:
    """The event this code opens, by either accepted code shape.

    Checked in the same order `_join` accepts them, so a code that resolves here is a code that will
    also be accepted at the door: the event's own invite code first, then a kiosk link. A kiosk link
    is looked up by hash and therefore costs a get rather than a query.
    """
    hits = list(
        fs.db()
        .collection("events")
        .where(filter=firestore.FieldFilter("access.codeHash", "==", code_hash))
        .limit(1)
        .stream()
    )
    if hits:
        return hits[0].id, (hits[0].to_dict() or {})

    snap = fs.host_link_ref(code_hash).get()
    if snap.exists:
        link = snap.to_dict() or {}
        event_id = str(link.get("eventId") or "")
        expires_at = link.get("expiresAt")
        fresh = isinstance(expires_at, dt.datetime) and dt.datetime.now(dt.timezone.utc) <= expires_at
        if (
            event_id
            and str(link.get("grants") or "host") == "member"
            and not link.get("revoked")
            and fresh
        ):
            return event_id, (fs.get_event(event_id) or {})
    return None


@_join_code_router.post("/join-code", response_model=JoinCodeResponse)
async def resolve_join_code(
    req: JoinCodeRequest, principal: Principal = Depends(caller)
) -> JoinCodeResponse:
    """Turn an invite code into the event it opens, so `/join` knows where to send someone.

    **A wrong code and a code for a nonexistent event return the identical error.** Distinguishing
    them would turn this endpoint into an oracle for "is this a real code", which is the one thing a
    code-entry box must not be. `join` says `BAD_CODE` for the same case and in the same words.

    Resolving is not joining, and deliberately grants nothing: no membership claim is minted here. The
    caller follows up with `POST /v1/events/{eventId}/join` carrying the same code, which is where the
    status check, the seat cap and the claim grant live. An open event's guests never reach this path
    at all — they arrive on a QR deep link that already carries the eventId.
    """
    _rate_limit_code_lookup(principal.uid)

    found = _event_for_code(_code_hash(req.code.strip()))
    if not found:
        raise errors.forbidden("BAD_CODE", "that invite code is not valid")
    event_id, event = found
    log.info("join_code_resolved", event_id=event_id, uid=principal.uid)
    return JoinCodeResponse(eventId=event_id, eventName=event.get("name"))
