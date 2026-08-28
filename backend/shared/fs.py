"""Firestore access: one client per process, plus the document-path vocabulary.

Every path in the system is event-scoped (`events/{eventId}/…`, spec 03 §1). Centralising the
refs here means a typo'd collection name is a single-file fix, and it keeps the tree in spec 03
greppable from code.
"""

from __future__ import annotations

import functools
from enum import Enum
from typing import Any

from google.cloud import firestore

from . import log
from .settings import settings
from .ulid import new_ulid

SERVER_TIMESTAMP = firestore.SERVER_TIMESTAMP
DELETE_FIELD = firestore.DELETE_FIELD


@functools.lru_cache(maxsize=1)
def db() -> firestore.Client:
    return firestore.Client(project=settings().project)


# ---------------------------------------------------------------- refs


def event_ref(event_id: str) -> firestore.DocumentReference:
    return db().collection("events").document(event_id)


def media_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("media")


def media_ref(event_id: str, media_id: str) -> firestore.DocumentReference:
    return media_col(event_id).document(media_id)


def hashes_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("hashes")


def hash_ref(event_id: str, md5_hex: str) -> firestore.DocumentReference:
    """Exact-content dedupe register (spec 01 §5). Scoped per event, not global."""
    return hashes_col(event_id).document(md5_hex)


def guests_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("guests")


def guest_ref(event_id: str, uid: str) -> firestore.DocumentReference:
    return guests_col(event_id).document(uid)


def people_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("people")


def person_ref(event_id: str, person_id: str) -> firestore.DocumentReference:
    return people_col(event_id).document(person_id)


def enrollments_col(event_id: str) -> firestore.CollectionReference:
    """`enrollments/{personId}` — the selfie embedding, and *only* that (spec 02 §4).

    Kept out of the person document on purpose. Firestore security rules cannot hide a field: any
    rule that lets a guest read `people/{p}` for a display name and a VIP tier — which the kiosk
    leaderboard, the album header and the Highlights ranking all need — would hand every guest at
    the event a copy of everyone else's face template. So the biometric lives in its own collection
    that no client rule grants at all, and only `worker-face` and `api` (via `shared/faces.py`) ever
    read it. Same reasoning applies to anything else genuinely private about a person: put it here
    or under `people/{p}/private/…`, both of which are deny-all in `firestore.rules`.
    """
    return event_ref(event_id).collection("enrollments")


def enrollment_ref(event_id: str, person_id: str) -> firestore.DocumentReference:
    return enrollments_col(event_id).document(person_id)


def reactions_col(event_id: str, person_id: str) -> firestore.CollectionReference:
    """`people/{personId}/reactions/{mediaId}` — spec 07 §1, the one client write in the entire
    system (`firestore.rules:159`). Read server-side only by `directors/story/taste.py`, which folds
    it into the deterministic tag-affinity vector and, every 15 new reactions, a Gemma taste memo.
    """
    return person_ref(event_id, person_id).collection("reactions")


def notices_col(event_id: str, person_id: str) -> firestore.CollectionReference:
    """Person-scoped notices — the "new device joined" card in a private album (spec 02 §3.2).

    Under the person rather than the event because that is exactly who may read it: the album
    owner. A host-facing notice is an `ops/` alert, and the two must never be the same collection.
    """
    return person_ref(event_id, person_id).collection("notices")


def faces_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("faces")


def face_ref(event_id: str, face_id: str) -> firestore.DocumentReference:
    return faces_col(event_id).document(face_id)


def claim_audits_col(event_id: str) -> firestore.CollectionReference:
    """`claimAudits/{claimId}` (spec 03 §1) — every claim, any size, host-visible and reversible."""
    return event_ref(event_id).collection("claimAudits")


def claim_audit_ref(event_id: str, claim_id: str) -> firestore.DocumentReference:
    return claim_audits_col(event_id).document(claim_id)


def claim_link_ref(code_hash: str) -> firestore.DocumentReference:
    """`claimLinks/{hash}` — spec 02 §3.1, and deliberately a *root* collection.

    The redemption endpoint (`POST /v1/claim`) receives a bare code and no event, so the hash has
    to be addressable without knowing the event. Only the hash is stored; a leaked database dump
    does not yield a working link.
    """
    return db().collection("claimLinks").document(code_hash)


def host_link_ref(code_hash: str) -> firestore.DocumentReference:
    """`hostLinks/{hash}` — spec 08 §1's host magic link, same shape as `claim_link_ref` and for the
    same reason: redemption (`POST /v1/host-claim`) receives a bare code with no event, so the hash
    has to be addressable without one, and only the hash is ever stored."""
    return db().collection("hostLinks").document(code_hash)


def event_creation_limiter_ref(uid: str) -> firestore.DocumentReference:
    """`eventCreationLimiter/{uid}` — spec 08 §1's "rate-limited" unauthenticated create.

    A root doc keyed by uid rather than anything event-scoped, because the thing being limited is
    one anonymous session creating many events, which by definition happens before any event exists
    to scope it to.
    """
    return db().collection("eventCreationLimiter").document(uid)


def bounties_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("bounties")


def bounty_ref(event_id: str, bounty_id: str) -> firestore.DocumentReference:
    return bounties_col(event_id).document(bounty_id)


def reels_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("reels")


def ops_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("ops")


def kiosk_playlist_ref(event_id: str) -> firestore.DocumentReference:
    """`kiosk/playlist` — the publisher's program (spec 04 §4).

    The one document in the system with `allow read: if true`: a kiosk is a TV in a venue, and
    making the wall depend on an auth session would be a way for it to go dark, not a control. It
    therefore holds only mediaIds that are already `public` plus the publisher's own ranking
    factors — never a uid, a display name or an unpublished item.
    """
    return event_ref(event_id).collection("kiosk").document("playlist")


def ledger_ref(event_id: str, doc: str) -> firestore.DocumentReference:
    """`ledger/{doc}` — the Story Director's aggregate state (spec 05 §1)."""
    return event_ref(event_id).collection("ledger").document(doc)


def director_state_ref(event_id: str) -> firestore.DocumentReference:
    """`ledger/directorState` — the Story Director's tick-to-tick working memory (spec 05 §1).

    Firestore rather than Agent Runtime Sessions, and the reasoning is HANDOFF §4.18's: this document
    holds the rolling 10-tick window, the last stage the director saw, the deferred reel commissions
    and the permanent coverage gaps the wrap report has to be honest about. All four are read inside
    transactions, are the host console's evidence surface, and must survive the director being
    redeployed — which is the definition of a system of record, not of a session cache. What *does*
    live in soft, probabilistic storage is the host's free-text taste (`directors/story/memory.py`),
    and nothing there gates a bounty, a point award or an exposure.
    """
    return ledger_ref(event_id, "directorState")


def coverage_stage_shards_col(event_id: str) -> firestore.CollectionReference:
    """`ledger/coverageShards/stages/{stageId}` — the incremental coverage ledger (spec 05 §1).

    A materialized view, not a cache. Recomputing coverage inside the tick is O(photos) — tens of
    seconds at five thousand photos, at which point a 30-second demo tick overlaps itself — so the
    counters are bumped once, inside the very transaction that already sets `status='indexed'`
    (`shared/coverage.py`, called from `shared/pipeline.py`). The tick then reads a handful of small
    documents and stays O(1) as the event grows.

    Sharded per stage for the same reason `ops/pulse_shards` is sharded per worker type (HANDOFF
    §4.13): one counter document for a whole wedding would be the one hot document in the system.
    """
    return ledger_ref(event_id, "coverageShards").collection("stages")


def coverage_stage_shard_ref(event_id: str, stage_id: str) -> firestore.DocumentReference:
    return coverage_stage_shards_col(event_id).document(stage_id)


def platform_doc(name: str) -> firestore.DocumentReference:
    """Platform-wide singletons (spec 11 §1.2/§1.5): liveEventCount, publicCreationEnabled."""
    return db().collection("platform").document(name)


def tick_ref(event_id: str) -> firestore.DocumentReference:
    """`ticks/{eventId}` — the director tick lease (spec 05 §1), a *root* collection.

    Root rather than event-scoped because it is infrastructure about an event rather than content
    within one, and because the thing it protects is the Scheduler's fan-out: one global job walks
    every live event, and the lease is what stops a slow tick and the next schedule from running two
    directors against the same event and double-issuing bounties. No client rule grants it.
    """
    return db().collection("ticks").document(event_id)


def publisher_lease_ref(event_id: str) -> firestore.DocumentReference:
    """`publisherLease/{eventId}` — per-event leader election for the kiosk playlist (spec 04 §4).

    Not a global singleton: `max-instances=1` on the publisher would serialise every concurrent
    event's playlist through one process, which is a correctness bottleneck disguised as a scale
    limit. The invariant that actually matters is "no two writers touch one event's playlist", and
    that is what this document enforces.
    """
    return db().collection("publisherLease").document(event_id)


# ---------------------------------------------------------------- helpers


def to_firestore(value: Any) -> Any:
    """`pydantic_model.model_dump(by_alias=True)`, made safe for `.set()`/`.update()`.

    `model_dump(mode="json")` is the tempting one-liner and the wrong one: it stringifies every
    datetime, and a stage window (`EventStage.startsAt`/`endsAt`) or a wrap report's `generatedAt`
    stored as a string can never be compared against a photo's `capturedAt` again (the same trap
    `scripts/dev_event.py::firestore_ready` exists to avoid — this is that function's production
    home, since `backend/api` writers need it too and should not import a dev script to get it).
    Enums still need converting, or the Firestore client raises on a value it does not recognise;
    datetimes and everything else pass through untouched.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: to_firestore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_firestore(v) for v in value]
    return value


def get_event(event_id: str) -> dict[str, Any] | None:
    snap = event_ref(event_id).get()
    return snap.to_dict() if snap.exists else None


def ops_alert(
    event_id: str,
    kind: str,
    message: str,
    *,
    media_id: str | None = None,
    severity: str = "warning",
    **extra: Any,
) -> str:
    """Write an ops alert — this is what puts the red badge on the host console.

    Never raises: an alert failing to write must not fail the handler that noticed the problem.
    """
    alert_id = new_ulid()
    payload: dict[str, Any] = {
        "kind": kind,
        "message": message,
        "severity": severity,
        "mediaId": media_id,
        "resolved": False,
        "createdAt": SERVER_TIMESTAMP,
        **extra,
    }
    try:
        ops_col(event_id).document(alert_id).set(payload)
    except Exception as exc:  # noqa: BLE001 - alerting must never mask the original failure
        log.error("ops_alert_write_failed", event_id=event_id, kind=kind, err=str(exc))
    else:
        log.warn("ops_alert", event_id=event_id, media_id=media_id, kind=kind, detail=message)
    return alert_id
