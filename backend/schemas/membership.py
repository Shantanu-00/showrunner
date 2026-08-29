"""Event membership — the wire shapes for `POST /v1/events/{eventId}/join` (spec 02 §1).

Membership is the *event* boundary and is a different axis from the consent rings: a ring answers
"who may see this photograph", membership answers "who is at this event at all". They compose, which
is why nothing here reaches `shared/visibility.py` and no media document gains a field.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.event import EventAccessMode


class JoinRequest(BaseModel):
    #: Required only on an invite-only event. Accepts either the host's invite code or a kiosk link's
    #: code — both are compared as sha256 hashes against a stored hash, never in plaintext.
    code: str | None = Field(default=None, max_length=128)


class JoinResponse(BaseModel):
    eventId: str
    #: Always `true` on a 200: the endpoint either admitted the caller or raised. It exists so a
    #: client can branch on the response body without inspecting the status code.
    joined: bool = True
    #: `false` when this uid had already come through the door — the signal a client uses to decide
    #: whether to show "welcome" copy, and the proof that a rejoin took no seat.
    newMember: bool = False
    mode: EventAccessMode = EventAccessMode.OPEN
    #: The seat cap and how much of it is used. Named "seats" everywhere a human can see, because the
    #: cap counts uids and spec 02 §1 deliberately gives one person several.
    seats: int | None = None
    seatsUsed: int = 0
    #: Every event this uid is now a member of — the same array that is in the `members` custom claim,
    #: returned so a client knows its token is stale and must be force-refreshed before any Firestore
    #: listener will succeed (`frontend/src/lib/firebase.ts::refreshClaims`).
    memberOf: list[str] = Field(default_factory=list)


class JoinCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class JoinCodeResponse(BaseModel):
    """What `/join` (the code-entry page) needs to know where to send someone.

    Deliberately thin: an event id and a display name. Resolving a code is *not* joining — the caller
    still has to `POST …/{eventId}/join` with the same code, which is where the seat cap, the status
    check and the claim grant happen. Keeping the two apart means a resolved code that turns out to
    belong to a full event fails at the door with the right message rather than half-succeeding here.
    """

    eventId: str
    eventName: str | None = None
