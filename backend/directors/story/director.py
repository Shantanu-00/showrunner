"""One tick of the Story Director — Validate → Expire → Arm → LEDGER → REASON → ACT.

This is spec 05 §1's control loop, and the ordering is the design rather than a convenience:

1. **Validate** the bounty submissions that finished the pipeline since the last tick, so the ledger
   the director reasons over already reflects the gaps its last bounties closed. Reasoning first and
   validating afterwards would make the director re-ask for a photograph it already has.
2. **Expire** what timed out, recording each as a permanent coverage gap the wrap report owes the host.
3. **Arm** the new stage's required moments if the stage changed — the anticipation half of spec 05 §2,
   which cannot wait for a statistical signal because a varmala lasts under a minute.
4. **LEDGER**: deterministic aggregation, O(1) in event size, no model.
5. **REASON**: the one model call, structured, guardrailed vocabulary, Model Armor in front of it.
6. **ACT**: re-derive every field of every action from Firestore, then execute or reject-and-log.

Two properties hold whatever happens inside:

- **The tick never takes a second lease.** It runs inside `api`'s `/internal/tick`, which already holds
  `ticks/{eventId}` for the duration (HANDOFF §4.20). A director that took its own lease would
  deadlock against the one protecting it; a director that extended that one would throttle the cadence
  it exists to serve.
- **The tick is recorded even when the model fails.** The session write happens in a `finally`, so a
  deflected prompt, a rate limit or a schema-invalid answer still leaves the deterministic work
  committed and the window advanced. A director that loses its memory whenever Gemini has a bad minute
  would re-arm the same bounties on the next pass.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi.concurrency import run_in_threadpool

from schemas.director import DirectorPlan
from services import gemini
from services.armor_plugin import ModelArmorPlugin
from shared import log
from shared.eventtime import EventCalendar
from shared.settings import STAGE_GAP_GRACE_MINUTES, TICK_IDLE_LOOKAHEAD_MINUTES
from shared.stages import as_dt

from . import (
    act,
    ledger as ledger_mod,
    memory,
    session as session_mod,
    validate,
    world as world_mod,
)
from .agent import prompt_parts, reason_agent

STAGE = "director"


def _is_idle(event_id: str, event: dict[str, Any], now: dt.datetime) -> bool:
    """Spec 13's overnight economy: nothing scheduled near now, nobody uploading, nothing open —
    so this tick does its deterministic steps and does not pay for a REASON call.

    Deliberately conservative, cheapest check first. Any condition it cannot be sure of keeps the
    director awake: a host holding a stage is actively directing; a stage without a window might be
    happening right now for all the schedule knows; an event with no stages at all keeps its
    pre-spec-13 behavior. Velocity is one count aggregation; the bounty scan only runs when
    everything cheaper already said "idle".
    """
    if event.get("stageOverride"):
        return False
    stages = list(event.get("stages") or [])
    if not stages:
        return False
    horizon_start = now - dt.timedelta(minutes=STAGE_GAP_GRACE_MINUTES)
    horizon_end = now + dt.timedelta(minutes=TICK_IDLE_LOOKAHEAD_MINUTES)
    for stage in stages:
        starts, ends = as_dt(stage.get("startsAt")), as_dt(stage.get("endsAt"))
        if starts is None or ends is None:
            return False  # an unscheduled stage keeps the director awake
        if starts <= horizon_end and ends >= horizon_start:
            return False
    if ledger_mod._velocity(event_id, now) > 0:  # noqa: SLF001 - same package, same read the ledger does
        return False
    if act.open_dedupe_keys(event_id):
        return False
    return True


async def run_tick(event_id: str, event: dict[str, Any], *, tick_id: str) -> dict[str, Any]:
    """Run one tick for one event. Returns the report the tick endpoint puts in its response body.

    Blocking Firestore work goes through `run_in_threadpool` and the model call is awaited on the
    event loop directly — `api` serves guests' upload requests from the same process at concurrency
    80, so a tick that blocked the loop for four seconds would stall every phone talking to that
    instance. (The GenAI client is bound to the loop it was created on, so the model call cannot be
    pushed to a thread; see `workers/curate/app.py`.)
    """
    started = dt.datetime.now(dt.timezone.utc)
    outcome = act.Outcome()
    plan: DirectorPlan | None = None
    usage = gemini.ModelUsage()
    error: str | None = None
    led: ledger_mod.Ledger | None = None
    gaps: list[dict[str, Any]] = []
    drift_note: tuple[str | None, int] | None = None
    archived_ids: list[str] = []
    idle = False

    state = await run_in_threadpool(session_mod.load, event_id)

    try:
        settled = await validate.settle(event_id, tick_id=tick_id, event=event)
        usage = usage + settled.usage

        expired, gaps = await run_in_threadpool(act.expire_bounties, event_id)
        outcome.expired = expired

        # Assignment timeouts release with the same deterministic step (spec 13 §6): an unanswered
        # personal ask becomes a broadcast, model not consulted. Runs before the idle check for the
        # same reason Expire does — an open assignment means the event is not idle anyway.
        await run_in_threadpool(act.release_stale_assignments, event_id)

        # Spec 13's idle economy. Checked *after* Validate and Expire, because awards never wait and
        # a timed-out bounty closes on time whatever the hour — and a tick that just expired one is
        # not idle, since the record of that expiry is written by the session step below.
        idle = not gaps and await run_in_threadpool(_is_idle, event_id, event, started)
        if idle:
            return {
                "mode": "idle",
                "validation": settled.as_report(),
                "expired": outcome.expired,
                "tokensIn": usage.tokensIn,
                "tokensOut": usage.tokensOut,
            }

        preferences = await memory.recall_host_preferences(event_id, event)
        # One document read, never a model call: the distillation itself runs at the *end* of the tick
        # (`api/internal.py::_do_work`), so what the director reads here is the paragraph written from
        # an earlier tick's counts. That staleness is the design — "warm" means the expensive step is
        # amortised across 25 photos and the read on the critical path is free. Degrades to "" and the
        # prompt block simply does not appear.
        venue = await run_in_threadpool(world_mod.recall_prose, event_id)
        led = await run_in_threadpool(
            ledger_mod.build,
            event_id,
            event,
            state,
            host_preferences=preferences,
            world_model=venue,
        )

        # Spec 13's evidence half of the advance rule: two ticks have to agree, so this tick reads
        # what the last one saw. Deterministic — the streak is arithmetic over the drift signal,
        # which is itself arithmetic over stored Curator distributions.
        if led.drift.signal and led.drift.top_stage_id:
            streak = state.drift_streak + 1 if state.drift_stage_id == led.drift.top_stage_id else 1
            led.drift_streak = streak
            drift_note = (led.drift.top_stage_id, streak)
        else:
            drift_note = (None, 0)

        # Fold newly-lapsed stages' uncovered moments into the permanent-gap record, exactly once —
        # the counterpart of the ledger's grace cutoff (spec 13).
        archived_ids, lapsed_gaps = act.archive_lapsed_stages(led, state)
        gaps = gaps + lapsed_gaps

        outcome.armed = await run_in_threadpool(
            act.arm_stage_moments, event_id, led, state, tick_id=tick_id
        )
        # The arming step runs between LEDGER and REASON and spends from the same per-tick budget
        # (spec 05 §1's "≤ 2 new bounties/tick" is read as a cap on issuance, not per mechanism), so
        # the model is told what it has left. Without this it proposes what the timetable already
        # fired, every proposal is correctly rejected, and its assessment takes credit anyway.
        led.armed_this_tick = [str(a["targetLabel"]) for a in outcome.armed]
        led.bounty_budget = max(0, act.DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK - len(outcome.armed))
        led.open_bounty_count += len(outcome.armed)

        plan, plan_usage, error = await _reason(event_id, led)
        usage = usage + plan_usage

        outcome = await run_in_threadpool(
            act.apply,
            event_id,
            led,
            plan,
            tick_id=tick_id,
            outcome=outcome,
            state=state,
        )

        report: dict[str, Any] = {
            "assessment": (plan.assessment if plan else "") or None,
            "validation": settled.as_report(),
            **outcome.as_report(),
            "gapsFound": len(led.gaps),
            # `open_bounty_count` already had the armed ones added above; the reasoned ones land here,
            # because a report that said "0 open" next to two freshly issued bounties reads as a bug.
            "openBounties": led.open_bounty_count + len(outcome.issued),
            "activeStage": led.active_stage_id,
            "drift": led.drift.as_line(),
            "tokensIn": usage.tokensIn,
            "tokensOut": usage.tokensOut,
        }
        if error:
            report["reasonError"] = error
        return report

    finally:
        stage_id = led.active_stage_id if led is not None else state.last_stage_id
        if not idle:
            # An idle tick leaves no session line: it did nothing worth the model's memory, and ten
            # overnight "[03:14] NO_OP" entries would push the evening the director actually worked
            # out of its own rolling window.
            calendar = led.calendar if led is not None else EventCalendar.of(event)
            summary = session_mod.TickSummary(
                tickId=tick_id,
                at=started,
                assessment=(plan.assessment if plan else (error or "tick produced no assessment")),
                actions=outcome.action_labels(),
                issued=len(outcome.issued) + len(outcome.armed),
                fulfilled=0,
                expired=len(outcome.expired),
                stageId=stage_id,
                day=calendar.stamp(started),
            )
            try:
                await run_in_threadpool(
                    session_mod.record,
                    event_id,
                    state,
                    summary,
                    stage_id=stage_id,
                    commissions=outcome.commissioned,
                    permanent_gaps=gaps,
                    drift=drift_note,
                    archived_stage_ids=archived_ids,
                )
            except Exception as exc:  # noqa: BLE001 - the tick already happened; losing the note is not fatal
                log.warn("director_session_write_failed", event_id=event_id, err=str(exc))

        log.line(
            "director",
            event_id=event_id,
            tick_id=tick_id,
            stage=stage_id,
            mode="idle" if idle else None,
            gaps=len(led.gaps) if led is not None else None,
            actions=",".join(outcome.action_labels()) or ("idle" if idle else "NO_OP"),
            rejected=len(outcome.rejected) or None,
            tokens_in=usage.tokensIn or None,
            tokens_out=usage.tokensOut or None,
            err=error,
            ms=int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000),
        )


async def _reason(
    event_id: str, led: ledger_mod.Ledger
) -> tuple[DirectorPlan | None, gemini.ModelUsage, str | None]:
    """The one model call. A failure is reported, never raised.

    Every failure mode here degrades to "this tick did its deterministic work and asked for nothing
    new", which is the conservative direction: an unissued bounty costs a photograph, and a tick that
    5xx'd would make Cloud Scheduler retry the whole fan-out across every live event because one
    event's prompt was unlucky (`api/internal.py`'s fourth design note).

    The Model Armor plugin sits in front of the model rather than beside it because this prompt is
    assembled from text that entered the system by several routes — the host's free-text standing
    preferences, the event name, the reviewed cultural glossary, a bounty title the director itself
    wrote two ticks ago. A surface check at each of those would be a list of places to remember.
    """
    try:
        plan, usage = await gemini.run_structured(
            reason_agent(),
            prompt_parts(led),
            DirectorPlan,
            stage=STAGE,
            plugins=[ModelArmorPlugin(surface="director", event_id=event_id)],
        )
        return plan, usage, None
    except gemini.ModelError as exc:
        log.warn("director_reason_failed", event_id=event_id, err=str(exc))
        return None, exc.usage, str(exc)[:300]
