"""Transactional leases — the one concurrency primitive in the control plane.

Two places need "exactly one of us, for this event, right now", and they need it for different
lengths of time, so the mechanism is written once here and parameterised rather than invented twice:

- **`ticks/{eventId}`** (spec 05 §1) — mutual exclusion around one director tick. Acquired at the
  top of the tick, released at the bottom. The TTL exists only so a process that dies mid-tick does
  not lock the event out forever.
- **`publisherLease/{eventId}`** (spec 04 §4) — leadership over one event's kiosk playlist for as
  long as an instance is alive and renewing. Expiry *is* the failover mechanism here: an instance
  that is scaled away stops renewing, and another one takes the event over inside the TTL.

Why a Firestore document rather than anything cleverer: the state that needs protecting already
lives in Firestore, so a lease held anywhere else could be valid while the write it authorises
fails, or vice versa. A transaction on a document in the same database cannot disagree with itself.

The holder id is a caller-chosen opaque string (a Cloud Run instance id, a tick id). Only equality
matters — a renewal must prove it is the same holder, or it is really an acquisition.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from google.cloud import firestore

from . import fs, log


@dataclass(frozen=True)
class Lease:
    """The outcome of an acquire/renew attempt."""

    ref: firestore.DocumentReference
    holder: str
    acquired: bool
    expires_at: dt.datetime | None = None
    #: Who has it, when we could not get it — the useful half of a failed acquisition.
    blocked_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.acquired


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _held_by(doc: dict[str, Any], now: dt.datetime) -> str | None:
    """The current holder, or None if the lease is free or expired."""
    if not doc.get("held"):
        return None
    expires = doc.get("expiresAt")
    if not isinstance(expires, dt.datetime):
        # A held lease with no readable expiry would be a permanent lock. Treat it as expired: a
        # duplicated tick is recoverable, an event whose director can never run again is not.
        return None
    if expires <= now:
        return None
    return str(doc.get("holder") or "unknown")


@firestore.transactional
def _acquire(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    holder: str,
    ttl: dt.timedelta,
    count: bool,
) -> tuple[bool, str | None, dt.datetime]:
    snap = ref.get(transaction=transaction)
    doc = snap.to_dict() or {}
    now = _now()

    current = _held_by(doc, now)
    if current is not None and current != holder:
        return False, current, now

    expires = now + ttl
    updates: dict[str, Any] = {
        "held": True,
        "holder": holder,
        "expiresAt": expires,
        "renewedAt": now,
    }
    if current is None:
        updates["acquiredAt"] = now
        if count:
            updates["acquisitions"] = firestore.Increment(1)
    transaction.set(ref, updates, merge=True)
    return True, None, expires


def acquire(
    ref: firestore.DocumentReference,
    holder: str,
    *,
    ttl_seconds: float,
    count: bool = True,
) -> Lease:
    """Take the lease, or renew it if this holder already has it. Never blocks, never waits.

    A failure to acquire is a normal, expected outcome — another instance is doing the work — so it
    is logged at info and the caller simply skips. `count` bumps an acquisition counter, which is
    what makes "the tick ran N times" visible on the document itself.
    """
    ok, blocked_by, expires = _acquire(
        fs.db().transaction(), ref, holder, dt.timedelta(seconds=ttl_seconds), count
    )
    if not ok:
        log.info("lease_busy", doc=ref.path, holder=holder, held_by=blocked_by)
        return Lease(ref, holder, False, blocked_by=blocked_by)
    return Lease(ref, holder, True, expires_at=expires)


@firestore.transactional
def _release(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    holder: str,
    fields: dict[str, Any],
) -> bool:
    snap = ref.get(transaction=transaction)
    doc = snap.to_dict() or {}
    if str(doc.get("holder") or "") != holder:
        # Someone else's lease. Releasing it would be worse than leaking ours: it would hand a
        # second writer permission while the first is still working.
        return False
    transaction.set(ref, {"held": False, "releasedAt": _now(), **fields}, merge=True)
    return True


def release(lease: Lease, **fields: Any) -> bool:
    """Give the lease back, recording whatever the caller wants remembered about the run.

    Releasing rather than waiting out the TTL is what keeps the cadence honest: a tick that
    finishes in 800 ms must not hold a 5-minute lease, or the next scheduled tick is a no-op.
    """
    if not lease.acquired:
        return False
    try:
        return _release(fs.db().transaction(), lease.ref, lease.holder, fields)
    except Exception as exc:  # noqa: BLE001 - a stuck release must not fail the work already done
        log.warn("lease_release_failed", doc=lease.ref.path, holder=lease.holder, err=str(exc))
        return False
