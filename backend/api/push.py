"""`POST …/push-token` / `DELETE …/push-token` — the guest's opt-in to being told what to photograph.

Two endpoints, one document (`guests/{uid}/private/push`, see `fs.guest_push_ref`). They exist as an
API surface rather than a client Firestore write for the same reason every other privileged write in
this project does: `firestore.rules` gives that whole subtree `allow read, write: if false`, so the
only way a token lands there is through a caller whose event membership was verified against a
Firebase ID token. A client that could write its own push document could also write somebody
else's, and a registration token is an address — the one thing on a guest record worth spoofing.

**Membership is required, deliberately.** A push subscription is a channel *into* a person's phone,
and the only reason this system has to open one is that they are at this event. `isMember` is the
same gate the bounty documents themselves sit behind, so nobody can subscribe to the missions of an
event they were never admitted to — which on an invite-only event would otherwise be a way to learn
that the event is happening at all, and when.

**Opting out is a real delete, not a flag.** `DELETE` removes the document; the next bounty's
`_tokens_for` simply does not find one. No `enabled: false` to keep in sync, and nothing left behind
for a later feature to accidentally start reading again. Revoking permission in the browser has the
same effect from the other end (the token stops working and `shared/push.py` prunes it on the send
that proves it), so the two directions converge on the same state without coordinating.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from shared import errors, log, push
from shared.auth import Principal, caller

router = APIRouter(prefix="/v1/events/{eventId}", tags=["push"])


class PushTokenRequest(BaseModel):
    """`platform` is telemetry, never routing — it is how "did anyone on iOS actually manage to
    subscribe" becomes answerable, given that iOS grants Web Push only to an installed PWA."""

    token: str = Field(min_length=16, max_length=4096)
    platform: str | None = Field(default=None, max_length=32)


@router.post("/push-token")
async def register_push_token(
    req: PushTokenRequest,
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, object]:
    """Store (or refresh) this session's Web Push registration token.

    Safe and expected to call on every app open. A registration token is not permanent — the browser
    rotates it, and a client that registered once and never again would go silently unreachable
    partway through a five-day event — so the client calls this whenever it holds a fresh token and
    this handler overwrites one document. Idempotent by construction: same uid, same path, last
    write wins.
    """
    if not principal.is_member_of(eventId):
        raise errors.forbidden(
            "NOT_A_MEMBER", "join this event before subscribing to its missions"
        )
    await _save(eventId, principal.uid, req)
    log.info("push_token_registered", event_id=eventId, platform=req.platform or "web")
    return {"subscribed": True}


@router.delete("/push-token")
async def revoke_push_token(
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, object]:
    """Forget this session's token. No membership check: letting somebody stop being contacted must
    never be the request that fails, and deleting a document keyed by the caller's own uid can only
    ever affect the caller."""
    push.delete_token(eventId, principal.uid)
    log.info("push_token_revoked", event_id=eventId)
    return {"subscribed": False}


async def _save(event_id: str, uid: str, req: PushTokenRequest) -> None:
    from fastapi.concurrency import run_in_threadpool

    await run_in_threadpool(
        push.save_token, event_id, uid, req.token.strip(), platform=req.platform
    )
