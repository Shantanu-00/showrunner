"""Firestore access: one client per process, plus the document-path vocabulary.

Every path in the system is event-scoped (`events/{eventId}/…`, spec 03 §1). Centralising the
refs here means a typo'd collection name is a single-file fix, and it keeps the tree in spec 03
greppable from code.
"""

from __future__ import annotations

import functools
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


def hash_ref(event_id: str, md5_hex: str) -> firestore.DocumentReference:
    """Exact-content dedupe register (spec 01 §5). Scoped per event, not global."""
    return event_ref(event_id).collection("hashes").document(md5_hex)


def guest_ref(event_id: str, uid: str) -> firestore.DocumentReference:
    return event_ref(event_id).collection("guests").document(uid)


def people_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("people")


def faces_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("faces")


def bounty_ref(event_id: str, bounty_id: str) -> firestore.DocumentReference:
    return event_ref(event_id).collection("bounties").document(bounty_id)


def ops_col(event_id: str) -> firestore.CollectionReference:
    return event_ref(event_id).collection("ops")


def platform_doc(name: str) -> firestore.DocumentReference:
    """Platform-wide singletons (spec 11 §1.2/§1.5): liveEventCount, publicCreationEnabled."""
    return db().collection("platform").document(name)


# ---------------------------------------------------------------- helpers


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
