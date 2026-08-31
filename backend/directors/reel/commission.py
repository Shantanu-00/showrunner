"""How a reel gets started — spec 06 §1: "commissions, not hardcoded agents".

Three callers, one function: the Story Director's ACT step (`COMMISSION_REEL`), the host console
(`POST /v1/events/{id}/reels`), and a stage ending. All of them get the same guardrails, which is the
point of having one entry: the surface that can *ask* for a reel is deliberately not the surface that
decides whether one is allowed.

The guardrails, in the order they are checked:

1. **The event must be able to have a reel at all.** No commissions on a `draft` or `wrapped` event, and
   none while `publicFrozen` — the panic button (spec 08 §5) has to stop the thing that would put new
   content on the wall, not just the wall.
2. **One in-flight commission per persona** (spec 06 §3). Checked against the `reels` collection rather
   than against a queue's concurrency dial, because the invariant is per persona per event and a dial is
   global; and because the Story Director's caller already holds the tick lease, so this read serialises
   with every other decision that tick makes.
3. **A daily commission ceiling per event.** Spec 06 §6 budgets ten reels × two versions ≈ $4; nothing in
   the spec stops a director on a 30-second cadence from asking for a reel every tick. `MAX_REELS_PER_DAY`
   is that stop, and it is not env-overridable for the same reason none of the Story Director's guardrails
   are (HANDOFF §9): a spend cap an operator can widen from a deploy flag is not a cap.

The document is written **before** the job is launched, and a launch failure leaves it there in `failed`
with an `ops/` alert. The alternative — launch first, write after — has a window where an execution is
running against a document that does not exist yet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.event import EventStatus
from schemas.reel import ReelPersona, ReelStatus
from shared import fs, jobs, log

from . import store

#: NOT spec-pinned. Spec 06 §6's budget is "a full event with 10 reels × avg 2 versions ≈ $4", so twenty
#: commissions is the spec's own arithmetic used as a ceiling. Recorded in HANDOFF §9.
MAX_REELS_PER_DAY = 20

#: Statuses an event must be in for a commission to be legal. `wrapping` is included on purpose — the
#: finale reel is commissioned exactly then (spec 08 §2).
COMMISSIONABLE = (EventStatus.LIVE.value, EventStatus.WRAPPING.value)


@dataclass
class Commissioned:
    ok: bool
    reel_id: str | None = None
    reason: str = ""


def commission(
    event_id: str,
    *,
    persona: ReelPersona,
    stage_id: str | None = None,
    person_id: str | None = None,
    reason: str = "",
    commissioned_by: str = "director",
    event: dict | None = None,
    launch: bool = True,
) -> Commissioned:
    """Create a reel commission and start its render job. Never raises."""
    doc = event if event is not None else (fs.get_event(event_id) or {})
    if not doc:
        return Commissioned(False, reason="event does not exist")
    if doc.get("status") not in COMMISSIONABLE:
        return Commissioned(False, reason=f"event is {doc.get('status')}, not live")
    if doc.get("publicFrozen"):
        return Commissioned(False, reason="public output is frozen")

    # `main_character` needs a subject; `stage_recap` needs a stage. Checked here rather than trusted
    # from whoever asked, so a director hallucinating a persona/target mismatch cannot spend a render.
    if persona is ReelPersona.MAIN_CHARACTER and not person_id:
        return Commissioned(False, reason="main_character needs a personId")
    if persona is ReelPersona.STAGE_RECAP and not stage_id:
        return Commissioned(False, reason="stage_recap needs a stageId")
    if stage_id is not None:
        stage_ids = {str(s.get("stageId")) for s in (doc.get("stages") or []) if s.get("stageId")}
        if stage_id not in stage_ids:
            return Commissioned(False, reason=f"stage {stage_id!r} is not a stage of this event")

    existing = store.in_flight_of_persona(event_id, persona, stage_id=stage_id)
    if existing:
        return Commissioned(False, reason=f"{persona.value} is already being produced ({existing})")

    if _commissions_today(event_id) >= MAX_REELS_PER_DAY:
        fs.ops_alert(
            event_id,
            "reel_budget_reached",
            f"the daily commission ceiling of {MAX_REELS_PER_DAY} reels has been reached; "
            "further commissions are refused until tomorrow",
            severity="warning",
        )
        return Commissioned(False, reason=f"daily ceiling of {MAX_REELS_PER_DAY} reels reached")

    # A standing event with no natural end (spec 13's global demo) sets
    # `reelCommissionEveryNMedia` instead of relying on the daily ceiling above, which bounds
    # spend per day but not per photo — a slow trickle of uploads would otherwise still earn a
    # fresh reel every tick the director looks. `None` (every real host's event) is a no-op.
    every_n = doc.get("reelCommissionEveryNMedia")
    since_last = int(doc.get("mediaSinceLastReel") or 0)
    if every_n is not None and since_last < every_n:
        return Commissioned(
            False, reason=f"only {since_last}/{every_n} new photos/videos since the last reel"
        )

    audience_ring = 1 if persona is ReelPersona.MAIN_CHARACTER else 2
    reel_id = store.create(
        event_id,
        persona=persona,
        stage_id=stage_id,
        person_id=person_id,
        audience_ring=audience_ring,
        commissioned_by=commissioned_by,
        reason=reason,
    )

    # Reset the moment the commission is recorded, not on render success — a launch failure below
    # still consumed the photos that earned this reel; a retry (`retry()`) reuses the same
    # commission rather than asking for a second one.
    if every_n is not None:
        fs.event_ref(event_id).update({"mediaSinceLastReel": 0})

    if not launch:
        return Commissioned(True, reel_id=reel_id, reason="created, not launched")

    try:
        execution = jobs.run_render(event_id, reel_id)
    except Exception as exc:  # noqa: BLE001 - the commission exists; the launch is what failed
        store.fail(event_id, reel_id, f"render job could not be started: {exc}")
        return Commissioned(False, reel_id=reel_id, reason=f"job launch failed: {exc}")

    if execution is None:
        # No render job deployed. The commission is real and recorded; a later `deploy/render.sh` plus a
        # replay is all it needs. Same posture `tasks.enqueue` takes for an unconfigured worker URL.
        store.patch(event_id, reel_id, failureReason="render job not deployed; commission is queued")
        return Commissioned(True, reel_id=reel_id, reason="recorded; no render job deployed")

    log.info("reel_launched", event_id=event_id, reel_id=reel_id, persona=persona.value)
    return Commissioned(True, reel_id=reel_id)


def _commissions_today(event_id: str) -> int:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    query = fs.reels_col(event_id).where(filter=FieldFilter("createdAt", ">=", since))
    return sum(1 for _ in query.stream())


def retry(event_id: str, reel_id: str) -> Commissioned:
    """Re-launch a commission that failed. Resumable: the pipeline skips whatever already landed.

    Deliberately manual (the host console's failed-reel card, or a curl on camera) rather than automatic:
    a reel that failed twice is usually failing for a reason a retry will hit again, and an auto-retrying
    render job is how an 8-vCPU job becomes a bill.
    """
    doc = store.get(event_id, reel_id)
    if doc is None:
        return Commissioned(False, reason="reel does not exist")
    if doc.get("status") == ReelStatus.PUBLISHED.value:
        return Commissioned(True, reel_id=reel_id, reason="already published")
    store.patch(event_id, reel_id, status=ReelStatus.DIRECTING.value, failureReason=None, progress=0)
    try:
        jobs.run_render(event_id, reel_id)
    except Exception as exc:  # noqa: BLE001
        store.fail(event_id, reel_id, f"render job could not be restarted: {exc}")
        return Commissioned(False, reel_id=reel_id, reason=str(exc))
    return Commissioned(True, reel_id=reel_id)
