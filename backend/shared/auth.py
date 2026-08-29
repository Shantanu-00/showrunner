"""Caller identity — Firebase ID tokens in, custom claims out.

The identity model (spec 02 §1) is three loosely-coupled layers: a `uid` (anonymous Firebase
session, enough to upload), a `personId` (a human at the event, needed only to *receive*
things), and the uid↔person join table. Everything trusted downstream arrives as **custom claims**
on the verified token: `personId`, `hosts` and `members`. Firestore rules read the same claims, so
server checks and rules agree by construction — no `get()` joins (rules allow ten per request and
each is billed, so a `get()`-based membership check would cap a gallery grid at ten photos).

**`hosts` and `members` are arrays, and that is load-bearing.** Both used to be — or would have
been — a single string. A scalar `host` claim silently revoked a host's access to their first event
the moment they created a second one, because `set_custom_user_claims` overwrites rather than
appends. The same trap applies to membership: a guest at two events on one phone is ordinary.

**Claims are capped at 1000 bytes total** by Firebase. A 26-character ULID plus JSON quoting and a
comma costs ~29 bytes, so the two arrays together hold roughly 35 events before a grant starts
failing — far beyond any real phone, and worth knowing rather than discovering. If it ever matters,
the fix is a membership document plus a `get()`-free denormalisation, not a longer claim.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import firebase_admin
from fastapi import Header, HTTPException, status
from firebase_admin import auth as fb_auth

from . import log
from .settings import settings


@functools.lru_cache(maxsize=1)
def _app() -> firebase_admin.App:
    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app(options={"projectId": settings().project})


def _event_ids(claims: dict[str, object], key: str, legacy_key: str | None = None) -> tuple[str, ...]:
    """Read an array claim, tolerating the scalar it used to be.

    `hosts` was `host: '<eventId>'` and `members` did not exist. ID tokens already minted in the
    wild keep working until they expire (an hour) or the session re-redeems, so a host who is
    holding a live console mid-event does not get logged out by a deploy. New grants only ever
    write the array form.
    """
    raw = claims.get(key)
    out = [str(v) for v in raw if v] if isinstance(raw, list) else []
    if legacy_key:
        legacy = claims.get(legacy_key)
        if isinstance(legacy, str) and legacy and legacy not in out:
            out.append(legacy)
    return tuple(out)


@dataclass(frozen=True)
class Principal:
    uid: str
    person_id: str | None = None
    #: Every event this uid hosts, and every event it has joined. Arrays, not scalars — see the
    #: module docstring for the bug that made them arrays and for the ~35-event claim ceiling.
    host_event_ids: tuple[str, ...] = ()
    member_event_ids: tuple[str, ...] = ()
    platform_admin: bool = False

    def is_host_of(self, event_id: str) -> bool:
        return event_id in self.host_event_ids

    def is_member_of(self, event_id: str) -> bool:
        """The server-side twin of `firestore.rules`'s `isMember(eventId)`, same three terms in the
        same order. Hosting an event implies membership of it; being the platform operator implies
        membership of every event (spec 11 §1.1's support role), and neither implies the other."""
        return (
            event_id in self.member_event_ids
            or self.is_host_of(event_id)
            or self.platform_admin
        )


def verify_bearer(authorization: str | None) -> Principal:
    """Verify `Authorization: Bearer <firebase id token>`; anonymous tokens are fine."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = fb_auth.verify_id_token(token, app=_app())
    except Exception as exc:  # noqa: BLE001 - any verification failure is a 401, details logged
        log.warn("token_verify_failed", err=type(exc).__name__)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    return Principal(
        uid=claims["uid"],
        person_id=claims.get("personId"),
        host_event_ids=_event_ids(claims, "hosts", legacy_key="host"),
        member_event_ids=_event_ids(claims, "members"),
        platform_admin=bool(claims.get("platformAdmin")),
    )


async def caller(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the authenticated caller, anonymous or not."""
    return verify_bearer(authorization)


def custom_claims(uid: str) -> dict[str, object]:
    """This uid's current custom claims, as the Admin SDK sees them.

    Read separately from `merge_custom_claims` because reversing a claim has to be *conditional*:
    clearing `personId` unconditionally when a host denies a claim would strip the identity of a
    guest who had already been approved as somebody else earlier (a second, denied enrollment
    attempt is a perfectly ordinary thing for a guest to make). The caller compares before it clears.
    """
    return dict(fb_auth.get_user(uid).custom_claims or {})


def merge_custom_claims(uid: str, **updates: object) -> dict[str, object]:
    """Grant/clear one claim without wiping the others.

    `firebase_admin.auth.set_custom_user_claims` replaces the *entire* claims object — there is no
    partial update on the wire. Every claim-granting path in this codebase used to call it directly
    with only the claim it cared about, which means granting `host` to a uid that already carries an
    enrolled guest's `personId` would silently erase that guest's identity, and vice versa (a host
    who later selfie-enrolls at their own event would lose their `host` claim). Read-merge-write here
    once, used everywhere a claim is granted. `None` deletes a key instead of writing it.
    """
    current = dict(fb_auth.get_user(uid).custom_claims or {})
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    fb_auth.set_custom_user_claims(uid, current)
    return current


#: How many events one uid may host or belong to. Firebase caps custom claims at 1000 bytes for the
#: *whole* object, and this uid also carries `personId` and possibly `platformAdmin`; a 26-character
#: ULID costs ~29 bytes of JSON inside an array. 32 each is the honest ceiling with headroom, and
#: hitting it means something is wrong (a scripted joiner, a shared kiosk browser) rather than that a
#: guest is unusually social — so the append is refused loudly instead of silently truncating, which
#: would revoke access to whichever event happened to be first.
MAX_CLAIM_EVENTS = 32


class TooManyEventClaims(Exception):
    """Raised by `grant_event_claim` at `MAX_CLAIM_EVENTS`. Translated to HTTP by the caller — this
    module deliberately knows nothing about FastAPI status codes beyond the 401 above."""

    def __init__(self, key: str) -> None:
        super().__init__(f"this device is already in {MAX_CLAIM_EVENTS} events ({key})")
        self.key = key


def grant_event_claim(uid: str, key: str, event_id: str) -> tuple[str, ...]:
    """Append `event_id` to the `hosts` or `members` array claim. Idempotent.

    Rides `merge_custom_claims` (read-merge-write) so it can never clobber `personId`, and skips the
    write entirely when the event is already there — which is what makes `POST /join` safe to call on
    every page load, and what stops a rejoin from costing an Admin SDK round trip.
    """
    current = _event_ids(custom_claims(uid), key, legacy_key="host" if key == "hosts" else None)
    if event_id in current:
        return current
    if len(current) >= MAX_CLAIM_EVENTS:
        raise TooManyEventClaims(key)
    updated = (*current, event_id)
    # `host` (the old scalar) is dropped in the same write: it has been folded into the array above,
    # and leaving both would mean two sources of truth for the same question.
    extra: dict[str, object] = {"host": None} if key == "hosts" else {}
    merge_custom_claims(uid, **{key: list(updated)}, **extra)
    return updated
