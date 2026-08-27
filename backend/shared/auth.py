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
