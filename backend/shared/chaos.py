"""Deliberate, bounded failure injection — the switch behind the chaos demo beat.

`events/{eventId}/ops/chaos` with `{failNext: 3}` makes the next three task deliveries of the
named stage fail with a 500. Cloud Tasks then does what it is configured to do: retries with
10 s→300 s backoff, at the queue's dispatch rate, while uploads keep arriving. Nothing is lost,
which is the whole claim being demonstrated — and it is demonstrated on the real deployment with
the real queue rather than a slide.

Two details that matter more than the feature:

- **The counter is decremented in a transaction.** With `max-concurrent=10` on the classify queue,
  a plain read-then-write would let ten workers all see `failNext: 3` and all fail, turning "fail
  the next three" into "fail everything in flight". The demo has to be as bounded as it claims.
- **The doc carries no `resolved` or `createdAt` field.** It shares the `ops/` collection with real
  alerts, and the host console lists those with `where('resolved','==',false)`. A Firestore equality
  filter only matches documents that *have* the field, so omitting it keeps a testing artefact off
  the host's alert badge without needing a second collection or a filter exception.

Honoured only on `protected_demo` / `internal_dev` events. A guest's wedding is not a test bed,
and the gate means an accidentally-left-behind doc cannot do damage.
"""

from __future__ import annotations

from typing import Any

from google.cloud import firestore

from schemas.event import EventClass

from . import fs, log

CHAOS_DOC = "chaos"

_ALLOWED_CLASSES = frozenset({EventClass.PROTECTED_DEMO.value, EventClass.INTERNAL_DEV.value})


@firestore.transactional
def _consume(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    stage: str,
) -> str | None:
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return None
    doc = snap.to_dict() or {}

    stages = doc.get("stages")
    if stages and stage not in stages:
        return None

    try:
        remaining = int(doc.get("failNext") or 0)
    except (TypeError, ValueError):
        return None
    if remaining <= 0:
        return None

    transaction.update(ref, {"failNext": remaining - 1})
    return str(doc.get("reason") or "injected failure (ops/chaos)")


def should_fail(event_id: str, stage: str, event: dict[str, Any] | None = None) -> str | None:
    """Return a failure reason if this delivery has been chosen to fail, else None.

    Never raises: a chaos switch that can break the pipeline by *reading* it would be a liability
    on every request, including the ones the judges care about.
    """
    if (event or {}).get("class") not in _ALLOWED_CLASSES:
        return None
    try:
        reason = _consume(fs.db().transaction(), fs.ops_col(event_id).document(CHAOS_DOC), stage)
    except Exception as exc:  # noqa: BLE001 - the switch must never take the worker down
        log.warn("chaos_read_failed", event_id=event_id, stage=stage, err=str(exc))
        return None
    if reason:
        log.warn("chaos_injected", event_id=event_id, stage=stage, reason=reason)
    return reason
