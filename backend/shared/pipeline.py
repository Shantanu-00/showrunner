"""Stage lifecycle shared by every perception worker: claim → do the work → complete or fail.

The three perception stages (`curate`, `faces`, `safety`) run in parallel, from three separate
queues, against the same document. What they have in common is not the work — it is everything
around it, and all of that is failure handling, so it lives here once:

- **Claim** absorbs a duplicate Cloud Tasks delivery and refuses to spend money on media that has
  already been rejected, deleted or deduped.
- **Complete** stamps the stage `done`, sums token usage, derives `status='indexed'` when the last
  stage lands, bumps the Story Director's coverage counters on that transition, and recomputes
  visibility — one transaction, because a viewer must never observe a document whose verdict and
  exposure disagree, and because a coverage ledger that can drift from the media it counts is a
  ledger the director cannot reason from.
- **Fail** implements spec 03 §6's taxonomy: transient failures go back to the queue (max 5
  attempts, the queue owns the backoff), permanent ones are absorbed on the spot with the stage's
  conservative default so a poisoned photo costs exactly one pass instead of a retry storm.

Two deliberate asymmetries in the failure path, both about what "conservative" means:

- *Transient, attempts exhausted* → stage `failed`, media `quarantined`, `ops/` alert at `error`.
  We do not know what the item is, so it stops here and a human is told (spec 03 §3).
- *Permanent* → stage `failed_permanent`, conservative default applied, `ops/` alert at `warning`,
  status untouched. We do know: the model refused or returned garbage twice. The item keeps its
  uploader's album and the event pool and never reaches `indexed`, so no public surface will serve
  it — a degraded item, not a lost one. Quarantining here would hide a guest's own photo from them
  because a language model had an opinion, which is exactly the failure mode spec 04 §1 forbids.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from google.cloud import firestore

from schemas.common import MediaStatus, Stage, StageState

from . import coverage, fs, log
from .settings import MAX_STAGE_ATTEMPTS
from .visibility import recompute_visibility

#: Statuses from which no stage may run again. `quarantined` is deliberately absent: the replay
#: endpoint (spec 03 §6) exists to re-run exactly those stages.
_CLOSED_STATUSES = frozenset({MediaStatus.REJECTED.value, MediaStatus.ABANDONED.value})

#: Stage states that mean the work is finished, one way or another.
_SETTLED_STATES = frozenset({StageState.DONE.value, StageState.FAILED_PERMANENT.value})


@dataclass(frozen=True)
class Claim:
    """The result of trying to take a stage. `outcome == "claimed"` is the only one that works."""

    outcome: str  # claimed | settled | closed | duplicate | missing
    media: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == "claimed"


def retry_count(request: Request) -> int:
    """How many times Cloud Tasks has already retried this task (0 on the first delivery)."""
    raw = request.headers.get("X-CloudTasks-TaskRetryCount")
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def is_last_attempt(claim: Claim, request: Request | None = None) -> bool:
    """True when a transient failure now means the item is out of retries.

    Both counters are consulted and the larger wins. The queue's header is authoritative for
    dispatch attempts, but it is absent on a manual replay, and the document counter survives a
    queue being drained and recreated. Over-counting costs a quarantine that a replay undoes;
    under-counting costs a task that retries forever.
    """
    attempts = claim.attempts
    if request is not None:
        attempts = max(attempts, retry_count(request) + 1)
    return attempts >= MAX_STAGE_ATTEMPTS


# ---------------------------------------------------------------- claim


@firestore.transactional
def _claim(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    stage: str,
) -> Claim:
    """Take the stage, or explain why not.

    There is no in-flight lease here on purpose. Cloud Tasks is at-least-once, so a second
    delivery of a task already running is possible; a lease would suppress it, but it would also
    suppress the legitimate retry of a worker that crashed mid-flight, stranding the stage as
    `pending` until the hourly sweep. Spec 03 §6 chooses the other side of that trade — "re-run
    overwrites identically (idempotent)" — so the guard that matters is the settled-state check
    below, which is what stops an actual storm. The worst case is one extra Gemini call.
    """
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return Claim("missing")
    media = snap.to_dict() or {}

    if media.get("status") in _CLOSED_STATUSES or media.get("deleted"):
        return Claim("closed", media)
    if media.get("duplicateOf"):
        # Byte-identical to media already in this event: it inherits the canonical's results and
        # buys no perception (spec 01 §5). Dedupe only saves money if the saving happens here.
        return Claim("duplicate", media)
    if (media.get("stages") or {}).get(stage) in _SETTLED_STATES:
        return Claim("settled", media)

    attempts = int((media.get("attempts") or {}).get(stage) or 0) + 1
    transaction.update(
        ref,
        {
            f"attempts.{stage}": attempts,
            f"stageTimings.{stage}.startedAt": fs.SERVER_TIMESTAMP,
        },
    )
    return Claim("claimed", media, attempts)


def claim_stage(event_id: str, media_id: str, stage: Stage) -> Claim:
    claim = _claim(fs.db().transaction(), fs.media_ref(event_id, media_id), stage.value)
    if not claim.ok:
        log.info(
            "stage_skipped",
            stage=stage.value,
            event_id=event_id,
            media_id=media_id,
            reason=claim.outcome,
        )
    return claim


# ---------------------------------------------------------------- derived status


def _derive_status(media: dict[str, Any]) -> dict[str, Any]:
    """`status='indexed'` when every stage this item has reached `done` (spec 03 §3).

    Read off the document's own `stages` map rather than a per-kind list, so photos (thumb, curate,
    faces, safety) and videos (video_prep, then the same three) need no branch — intake and
    `video-prep` already wrote exactly the keys that apply.
    """
    stages = media.get("stages") or {}
    if not stages or not all(state == StageState.DONE.value for state in stages.values()):
        return {}
    if media.get("status") == MediaStatus.INDEXED.value:
        return {}
    if media.get("status") in _CLOSED_STATUSES:
        return {}
    return {"status": MediaStatus.INDEXED.value, "indexedAt": fs.SERVER_TIMESTAMP}


# ---------------------------------------------------------------- complete / fail


def _settle(
    event_id: str, media_id: str, updates: dict[str, Any], event: dict[str, Any] | None
) -> str | None:
    """Commit a stage outcome, the derived status, the coverage bump and the visibility consequence
    in one write.

    The coverage counters (spec 05 §1, `shared/coverage.py`) ride this transaction rather than taking
    one of their own, and the trigger is the *transition* into `indexed` rather than the state: the
    `transitioned` flag is set by the derive hook, which returns an update exactly once per item and
    never again. A replayed stage, a re-delivered task and a host re-review therefore all bump
    nothing, with no separate idempotency key to keep in sync. The flag is recomputed on every
    transaction attempt, so an optimistic-concurrency retry cannot double-count either.
    """
    transitioned = False

    def derive(media: dict[str, Any]) -> dict[str, Any]:
        nonlocal transitioned
        derived = _derive_status(media)
        transitioned = bool(derived)
        return derived

    def side_effect(transaction: Any, media: dict[str, Any]) -> None:
        if transitioned:
            coverage.bump(transaction, event_id, media)

    return recompute_visibility(
        event_id,
        media_id,
        event=event,
        extra=updates,
        derive=derive,
        side_effect=side_effect,
    )


def complete_stage(
    event_id: str,
    media_id: str,
    stage: Stage,
    *,
    fields: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    started: dt.datetime | None = None,
    **log_fields: Any,
) -> str | None:
    """Mark `stage` done, commit the worker's `fields`, and recompute visibility.

    `fields` is written as given, so a worker hands over whole blocks (`{"curator": {...}}`) rather
    than dotted per-key paths: a re-run then replaces its own block wholesale instead of leaving
    half of a previous attempt's answer merged underneath a new one.
    """
    updates: dict[str, Any] = dict(fields or {})
    updates[f"stages.{stage.value}"] = StageState.DONE.value
    updates[f"stageTimings.{stage.value}.doneAt"] = fs.SERVER_TIMESTAMP
    updates.update(usage or {})

    visibility = _settle(event_id, media_id, updates, event)
    log.stage(
        "done",
        stage=stage.value,
        event_id=event_id,
        media_id=media_id,
        ms=elapsed_ms(started),
        visibility=visibility,
        **log_fields,
    )
    return visibility


def fail_stage(
    event_id: str,
    media_id: str,
    stage: Stage,
    *,
    reason: str,
    permanent: bool,
    defaults: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    started: dt.datetime | None = None,
    **log_fields: Any,
) -> str | None:
    """Settle a stage that will not complete — see this module's docstring for the two shapes.

    `defaults` is the stage's conservative fallback (spec 03 §6): Curator writes aestheticScore 0
    with `needsReview`, Guardian writes `host_review`. It is committed in the same transaction as
    the failure flag, because a `failed` stage with no default is a document that later code reads
    as "no opinion yet" and treats optimistically.
    """
    state = StageState.FAILED_PERMANENT if permanent else StageState.FAILED
    updates: dict[str, Any] = dict(defaults or {})
    updates[f"stages.{stage.value}"] = state.value
    updates[f"stageTimings.{stage.value}.doneAt"] = fs.SERVER_TIMESTAMP
    updates[f"stageErrors.{stage.value}"] = reason[:500]
    if not permanent:
        updates["status"] = MediaStatus.QUARANTINED.value

    visibility = _settle(event_id, media_id, updates, event)
    fs.ops_alert(
        event_id,
        f"stage_{state.value}",
        f"{stage.value} {state.value}: {reason[:300]}",
        media_id=media_id,
        severity="warning" if permanent else "error",
        stage=stage.value,
        replayable=True,
    )
    log.stage(
        state.value,
        stage=stage.value,
        event_id=event_id,
        media_id=media_id,
        ms=elapsed_ms(started),
        visibility=visibility,
        err=reason[:300],
        **log_fields,
    )
    return visibility


def elapsed_ms(started: dt.datetime | None) -> int | None:
    if started is None:
        return None
    return int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000)
