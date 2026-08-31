"""Web Push delivery for the director's asks — the missing half of the bounty mechanism.

Every other piece of the crowd-directing loop was already built: the ledger notices a gap, the tick
issues a bounty under its guardrails, the PWA banners it and the kiosk puts a wanted poster on the
wall. What none of that could do was **reach a phone in somebody's pocket.** `BountyBanner` is a
Firestore listener on a live tab, so a guest who closed the app and walked to dinner was never told
anything — which made `BOUNTY_ASSIGN_TIMEOUT_MINUTES` a near-certainty rather than a fallback, and
made the whole "the agent directs the crowd" claim depend on the crowd happening to be looking at
their screens. This module closes that.

Five decisions here are load-bearing.

**1. It is Firebase Cloud Messaging, and the client half is standard Web Push.** No third-party
service, no self-hosted VAPID signing: `firebase-admin` is already a dependency (it mints the custom
claims), FCM is already enabled on the project, and the browser API is the W3C one. The client's
registration token is obtained with the Firebase JS SDK's `getToken` against a VAPID public key from
the project's own Cloud Messaging settings, and the service worker that displays the notification
(`frontend/public/firebase-messaging-sw.js`) deliberately imports **nothing** — see its own header.

**2. A registration token is an address, so it lives where addresses live.** Not on `guests/{uid}`:
that document is `allow read: if isMember(eventId)` because the kiosk leaderboard streams the whole
collection, and its rule says in as many words *"No email, no phone, no token."* Tokens go to
`guests/{uid}/private/push`, under the same deny-all-to-every-client `private/` pattern
`people/{id}/private/profile` already uses for `uidLinks` and the taste memo. Nobody but `api` and
this module ever reads one. See `fs.guest_push_ref`.

**3. Sending is off the critical path, unconditionally.** `notify_bounty` never raises. A bounty
document is the system of record and a notification is a courtesy about it; an FCM outage, a quota
error or a malformed token must not fail a tick, must not roll back a bounty, and must not cost the
guest their points. Same posture as the Event Diary (spec 13 §8) and for the same reason. Every
failure path logs and returns.

**4. The audience is resolved from the bounty's own `audience` field, deterministically.** There is
no second notion of "who should hear about this" — `assignee` goes to one uid, `near_stage` to the
guests `lastSeenAt` already puts inside the window (the same query `act.resolve_assignee` and
`ledger._active_guests` use), `all` to everyone. A model never picks a recipient here any more than
it picks an assignee. And targeting is still **delivery only**: spec 13 §6's invariant is that
whoever submits the fulfilling photo gets paid, so a push that reaches the wrong pocket costs
nothing and a push that reaches nobody loses only the prompt.

**5. Dead tokens are pruned on the failure that proves they are dead, not on a schedule.** A phone
that cleared its site data, an uninstalled PWA and a rotated token all surface as `UNREGISTERED` or
`InvalidArgument` on send. Deleting exactly those (and never a `Unavailable`, which is FCM being
briefly unhappy, not the device being gone) means the collection self-cleans with no sweep case, no
TTL and no second mechanism to keep in sync.

Not built, deliberately: no notification for points awarded, a published reel or a claim approval.
Each is a real product idea and each would need its own opt-in reasoning; a permission a guest
granted to be told what to photograph should not silently become a marketing channel.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.bounty import BountyAudience

from . import fs, log
from .settings import NEAR_STAGE_WINDOW_MINUTES, settings

#: Hard ceiling on recipients for one bounty. An event is seat-capped in the low hundreds
#: (`INVITE_DEFAULT_SEATS`), so this is not a scale rail — it is a blast-radius rail: if a query ever
#: returns something unexpected, the cost of the mistake is bounded at one batch.
MAX_RECIPIENTS = 300

#: FCM error conditions that mean *this token will never work again*, and therefore that deleting it
#: is correct rather than merely convenient. Anything else — including every transient — is left
#: alone, because a token deleted on a blip is a guest who silently stops being reachable.
_DEAD_TOKEN_ERRORS = ("UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH")


def _messaging() -> Any:
    """Import `firebase_admin.messaging` lazily, after `shared.auth` has initialised the app.

    `shared/auth.py::_app()` is the one initialiser in the project and it runs on the first token
    verification. Importing messaging at module scope here would pull `firebase_admin` into every
    worker's import path (the perception workers never send a notification) and would risk touching
    the SDK before that app exists.
    """
    from firebase_admin import messaging  # noqa: PLC0415 - see docstring

    from .auth import _app  # noqa: PLC0415

    _app()
    return messaging


# ---------------------------------------------------------------- the token store


def save_token(event_id: str, uid: str, token: str, *, platform: str | None = None) -> None:
    """Upsert this session's registration token. Idempotent: the same token re-registers as a touch.

    Called from `POST /v1/events/{eventId}/push-token` on every app open, because a registration
    token is not permanent — the browser may rotate it, and a client that only registered once would
    go quietly unreachable. Overwriting one document per uid is the cheapest possible way to make
    the freshest token always the stored one.
    """
    fs.guest_push_ref(event_id, uid).set(
        {
            "token": token,
            "platform": (platform or "web")[:32],
            "updatedAt": fs.SERVER_TIMESTAMP,
        }
    )


def delete_token(event_id: str, uid: str) -> None:
    """Forget this session's token — the opt-out, and what `DELETE …/people/me` sweeps up."""
    try:
        fs.guest_push_ref(event_id, uid).delete()
    except Exception as exc:  # noqa: BLE001 - opting out must never surface an error
        log.warn("push_token_delete_failed", event_id=event_id, err=str(exc))


def _tokens_for(event_id: str, uids: Iterable[str]) -> dict[str, str]:
    """`uid → token` for the uids that have one, in a single batched read.

    `get_all` rather than N gets: a broadcast on a 40-person trip is one round trip. Missing
    documents are simply absent from the result — most guests never grant permission, and that is a
    normal state, not a degraded one.
    """
    unique = [u for u in dict.fromkeys(uids) if u][:MAX_RECIPIENTS]
    if not unique:
        return {}
    out: dict[str, str] = {}
    try:
        refs = [fs.guest_push_ref(event_id, uid) for uid in unique]
        for snap in fs.db().get_all(refs):
            if not snap.exists:
                continue
            token = str((snap.to_dict() or {}).get("token") or "")
            if token:
                # `snap.reference.parent.parent` is `guests/{uid}` — the uid is not on the push
                # document itself, because storing a key inside the document it is keyed by is how
                # the two drift apart.
                parent = snap.reference.parent.parent
                if parent is not None:
                    out[parent.id] = token
    except Exception as exc:  # noqa: BLE001
        log.warn("push_tokens_read_failed", event_id=event_id, err=str(exc))
    return out


def _near_stage_uids(event_id: str, now: dt.datetime) -> list[str]:
    """Guests seen inside the nearStage window — the same definition `act.resolve_assignee` and
    `ledger._active_guests` use, read from the same field, so "who is here" means one thing."""
    since = now - dt.timedelta(minutes=NEAR_STAGE_WINDOW_MINUTES)
    try:
        query = (
            fs.guests_col(event_id)
            .where(filter=FieldFilter("lastSeenAt", ">=", since))
            .order_by("lastSeenAt", direction=firestore.Query.DESCENDING)
            .limit(MAX_RECIPIENTS)
        )
        return [snap.id for snap in query.stream() if not (snap.to_dict() or {}).get("banned")]
    except Exception as exc:  # noqa: BLE001
        log.warn("push_near_stage_query_failed", event_id=event_id, err=str(exc))
        return []


def _all_uids(event_id: str) -> list[str]:
    try:
        return [
            snap.id
            for snap in fs.guests_col(event_id).limit(MAX_RECIPIENTS).stream()
            if not (snap.to_dict() or {}).get("banned")
        ]
    except Exception as exc:  # noqa: BLE001
        log.warn("push_all_query_failed", event_id=event_id, err=str(exc))
        return []


def _recipients(
    event_id: str, audience: BountyAudience, assignee_uid: str | None, now: dt.datetime
) -> list[str]:
    if audience is BountyAudience.ASSIGNEE:
        # An assigned bounty banners for exactly one person until the timeout releases it (spec 13
        # §6), so notifying anyone else would contradict the client the guest is holding.
        return [assignee_uid] if assignee_uid else []
    if audience is BountyAudience.NEAR_STAGE:
        return _near_stage_uids(event_id, now)
    return _all_uids(event_id)


# ---------------------------------------------------------------- sending


def notify_bounty(
    event_id: str,
    *,
    bounty_id: str,
    title: str,
    copy: str,
    points: int,
    audience: BountyAudience,
    assignee_uid: str | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Deliver one bounty to its audience's phones. Returns how many were sent; never raises.

    The deep link lands on the camera tab with the bounty preselected —
    `/join/{eventId}?bounty={bountyId}` — which is the same URL `MissionsSheet`'s "Shoot now" button
    already produces, so tapping a notification and tapping the in-app banner arrive at exactly the
    same screen. Nothing about the payload is personalised beyond the audience: the title and the
    guest copy are the ones already on the bounty document, so what a phone buzzes with is provably
    the same sentence the wall is showing.
    """
    moment = now or dt.datetime.now(dt.timezone.utc)
    try:
        recipients = _recipients(event_id, audience, assignee_uid, moment)
        tokens = _tokens_for(event_id, recipients)
        if not tokens:
            return 0
        messaging = _messaging()
    except Exception as exc:  # noqa: BLE001 - see the module docstring: courtesy, never critical
        log.warn("push_prepare_failed", event_id=event_id, bounty=bounty_id, err=str(exc))
        return 0

    origin = (settings().app_origin or "").rstrip("/")
    link = f"{origin}/join/{event_id}?bounty={bounty_id}" if origin else f"/join/{event_id}"
    body = (copy or title or "").strip()[:180]

    uids = list(tokens)
    try:
        message = messaging.MulticastMessage(
            tokens=[tokens[uid] for uid in uids],
            # `data` only, no top-level `notification`: a top-level notification is displayed by the
            # browser *and* handed to the service worker on some engines, which is how one bounty
            # becomes two banners. The SW owns display, so the payload owns data.
            data={
                "kind": "bounty",
                "eventId": event_id,
                "bountyId": bounty_id,
                "title": f"+{points} pts · {title}"[:80] if points else title[:80],
                "body": body,
                "link": link,
                "tag": f"bounty-{bounty_id}",
            },
            webpush=messaging.WebpushConfig(
                # `urgency=high` is what asks the push service to wake a dozing device rather than
                # batch the message: a coverage gap is only fixable while the moment is still
                # happening, so a notification that arrives forty minutes late is worse than none.
                headers={"Urgency": "high", "TTL": "600"},
                fcm_options=messaging.WebpushFCMOptions(link=link),
            ),
        )
        response = messaging.send_each_for_multicast(message)
    except Exception as exc:  # noqa: BLE001
        log.warn("push_send_failed", event_id=event_id, bounty=bounty_id, err=str(exc))
        return 0

    sent = 0
    pruned = 0
    for uid, result in zip(uids, response.responses):
        if result.success:
            sent += 1
            continue
        if _is_dead_token(result.exception):
            delete_token(event_id, uid)
            pruned += 1

    log.line(
        "push",
        event_id=event_id,
        bounty=bounty_id,
        audience=audience.value,
        targeted=len(uids),
        sent=sent,
        pruned=pruned,
    )
    return sent


def _is_dead_token(exc: Any) -> bool:
    """Whether this send failure proves the token is permanently invalid (see `_DEAD_TOKEN_ERRORS`).

    Read off the exception's class name and text rather than by isinstance: `firebase_admin`'s
    `UnregisteredError` exists in current versions but the *reason* it is being checked here is
    version-independent, and a pin-sensitive isinstance chain would silently stop pruning after an
    SDK bump — leaving dead tokens to be retried on every bounty for the life of the event.
    """
    if exc is None:
        return False
    name = type(exc).__name__.upper()
    text = f"{name} {exc}".upper()
    return any(marker in text for marker in _DEAD_TOKEN_ERRORS) or "UNREGISTERED" in name
