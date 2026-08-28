"""Caller identity — Firebase ID tokens in, custom claims out.

The identity model (spec 02 §1) is three loosely-coupled layers: a `uid` (anonymous Firebase
session, enough to upload), a `personId` (a human at the event, needed only to *receive*
things), and the uid↔person join table. Only two identities are ever trusted downstream, and
both arrive as **custom claims** on the verified token: `personId` and `host`. Firestore rules
read the same claims, so server checks and rules agree by construction — no `get()` joins.
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


@dataclass(frozen=True)
class Principal:
    uid: str
    person_id: str | None = None
    host_event_id: str | None = None
    platform_admin: bool = False

    def is_host_of(self, event_id: str) -> bool:
        return self.host_event_id == event_id


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
        host_event_id=claims.get("host"),
        platform_admin=bool(claims.get("platformAdmin")),
    )


async def caller(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the authenticated caller, anonymous or not."""
    return verify_bearer(authorization)


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
