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

from . import act, ledger as ledger_mod, memory, session as session_mod, validate
from .agent import prompt_parts, reason_agent

STAGE = "director"


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

    state = await run_in_threadpool(session_mod.load, event_id)

    try:
        settled = await validate.settle(event_id, tick_id=tick_id)
        usage = usage + settled.usage

        expired, gaps = await run_in_threadpool(act.expire_bounties, event_id)
        outcome.expired = expired

        preferences = await memory.recall_host_preferences(event_id, event)
        led = await run_in_threadpool(
            ledger_mod.build, event_id, event, state, host_preferences=preferences
        )

        outcome.armed = await run_in_threadpool(
            act.arm_stage_moments, event_id, led, state, tick_id=tick_id
        )

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
            "openBounties": led.open_bounty_count,
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
        summary = session_mod.TickSummary(
            tickId=tick_id,
            at=started,
            assessment=(plan.assessment if plan else (error or "tick produced no assessment")),
            actions=outcome.action_labels(),
            issued=len(outcome.issued) + len(outcome.armed),
            fulfilled=0,
            expired=len(outcome.expired),
            stageId=stage_id,
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
            )
        except Exception as exc:  # noqa: BLE001 - the tick already happened; losing the note is not fatal
            log.warn("director_session_write_failed", event_id=event_id, err=str(exc))

        log.line(
            "director",
            event_id=event_id,
            tick_id=tick_id,
            stage=stage_id,
            gaps=len(led.gaps) if led is not None else None,
            actions=",".join(outcome.action_labels()) or "NO_OP",
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
