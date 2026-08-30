"""ACT — spec 05 §1's third step, and the place the model stops being in charge.

Every guardrail in spec 05 §1 is enforced here, against the event's real documents rather than against
what the plan claims about them:

    ≤ 2 new bounties per tick · ≤ 6 open at once · no duplicate bounty per (stage, moment, vip)
    points = clamp(basePoints × vipWeight(targetVip), 50, 300)
    stage advance auto-applies only if confidence ≥ 0.8 AND the scheduled window agrees ±45 min

The module is split into a **pure decision function** (`decide`) and a **writer** (`apply`), and the
split is the point. Spec 05 §5's fourth acceptance criterion is that guardrails hold under adversarial
model output — invalid actions rejected and logged, never applied — and a rule that can only be
exercised by deploying a service and coaxing a model into misbehaving is a rule nobody checks.
`decide` takes a ledger and a plan and returns verdicts, so `scripts/smoke_director.py
--guardrails-only` runs the whole adversarial table with no network, no Firestore and no spend, in the
same shape as `smoke_safety.py --gate-only` and `smoke_autonomy.py --program-only`.

The important property is not that the rules exist but that every field is *re-derived*: `targetVip`
is looked up in the people the host actually promoted, `targetStage` in the stages the event actually
has, `bountyId` in the bounties actually open. A plan naming a plausible-sounding stage is rejected
with a logged reason.

This module also owns the two mechanisms that are not the model's idea at all:

- **Arming** (spec 05 §2's anticipation half). A varmala lasts under a minute; reactive detection can
  only notice its absence once it is over. So the moment a stage becomes active, its required-moment
  bounties go live from the timeline prior — no reasoning, no model call, no waiting for a tick's
  worth of aggregate. *Anticipate the predictable, reconcile the statistical.*
- **Expiry.** An unfulfilled bounty past `expiresAt` becomes a permanent coverage gap recorded on the
  director state, because the wrap report's honesty depends on the system remembering what it asked
  for and did not get (spec 05 §3).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from schemas.bounty import (
    OPEN_STATUSES,
    Bounty,
    BountyAudience,
    BountyStatus,
    dedupe_key,
)
from schemas.director import ActionType, DirectorAction, DirectorPlan
from shared import fs, log
from shared.settings import (
    BOUNTY_DEFAULT_BASE_POINTS,
    BOUNTY_DEFAULT_TTL_MINUTES,
    BOUNTY_MAX_TTL_MINUTES,
    BOUNTY_MIN_TTL_MINUTES,
    BOUNTY_POINTS_MAX,
    BOUNTY_POINTS_MIN,
    DIRECTOR_MAX_ACTIVE_BOUNTIES,
    DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK,
    DRIFT_ADVANCE_TICKS,
    STAGE_ADVANCE_MIN_CONFIDENCE,
    STAGE_ADVANCE_WINDOW_MINUTES,
)
from shared.ulid import new_ulid

from . import ledger as ledger_mod
from . import session as session_mod

#: Escalation multiplier on the points already offered, re-clamped into the guardrail band. Spec 05 §3
#: says escalation means "more points, wider audience, kiosk takeover" without giving a number; 1.5×
#: is visibly a raise and cannot exceed the ceiling. Recorded in HANDOFF §9.
ESCALATION_MULTIPLIER = 1.5

#: Spec 06's personas. Named here rather than imported because spec 06's schemas are B4-S11's; this is
#: the vocabulary a commission may use, and S11 owns what happens to it.
REEL_PERSONAS = frozenset({"couple", "stage_recap", "guest_energy", "main_character"})

#: What an armed bounty says. Deliberately plain: it is a standing instruction from the timetable, not
#: a pitch a model wrote.
_ARMED_COPY = "Happening now: {label}. Grab a shot and send it in."

TITLE_LIMITS = (3, 80)
COPY_LIMITS = (10, 200)
ANNOUNCE_LIMITS = (3, 120)


def points_for(base_points: int, vip_weight: float) -> int:
    """`clamp(basePoints × vipWeight, 50, 300)` — spec 05 §1 verbatim, spec 11 §3.3 point 4.

    Pure, and the only place the arithmetic exists, because the number a guest sees on their banner,
    the number the kiosk poster shows and the number the award transaction adds to
    `guests/{uid}.points` all come from this one call. The guardrail band is the ceiling; tier is the
    reason a bride's-mother bounty outpays a generic one.
    """
    base = max(1, min(BOUNTY_POINTS_MAX, int(base_points)))
    scaled = int(round(base * max(0.0, float(vip_weight))))
    return max(BOUNTY_POINTS_MIN, min(BOUNTY_POINTS_MAX, scaled))


def ttl_for(minutes: int | None) -> int:
    return max(BOUNTY_MIN_TTL_MINUTES, min(BOUNTY_MAX_TTL_MINUTES, int(minutes or BOUNTY_DEFAULT_TTL_MINUTES)))


# ---------------------------------------------------------------- decisions (pure)


@dataclass(frozen=True)
class Decision:
    """One validated verdict on one proposed action. `ok` actions carry resolved parameters."""

    type: ActionType
    ok: bool
    reason: str = ""
    # ISSUE_BOUNTY
    stage: str | None = None
    moment: str | None = None
    person: ledger_mod.Person | None = None
    title: str = ""
    copy: str = ""
    base_points: int = BOUNTY_DEFAULT_BASE_POINTS
    points: int = 0
    ttl_minutes: int = BOUNTY_DEFAULT_TTL_MINUTES
    audience: BountyAudience = BountyAudience.ALL
    dedupe: str = ""
    # ESCALATE_BOUNTY
    bounty_id: str | None = None
    escalated_points: int = 0
    # PROPOSE_STAGE_ADVANCE
    to_stage: str | None = None
    confidence: float = 0.0
    auto_apply: bool = False
    evidence: str = ""
    # ANNOUNCE
    message: str = ""
    # COMMISSION_REEL
    persona: str | None = None
    stage_id: str | None = None

    def label(self) -> str:
        return f"{self.type.value}{'' if self.ok else ' (rejected)'}"


def decide(
    led: "ledger_mod.Ledger",
    plan: DirectorPlan,
    *,
    open_keys: set[str],
    open_count: int,
    budget: int,
    commissions: list[dict[str, Any]],
    now: dt.datetime,
) -> list[Decision]:
    """Validate a whole plan. No I/O, no writes — every input is already in the ledger.

    Budget and open-count are consumed as the list is walked, so two identical ISSUE_BOUNTY actions in
    one plan produce one acceptance and one rejection rather than two acceptances.
    """
    stage_ids = {s.stage_id for s in led.stages}
    people = {p.person_id: p for p in led.people}
    open_bounties = {b.bounty_id: b for b in led.bounties if b.status in OPEN_STATUSES}
    seen_keys = set(open_keys)
    seen_commissions = {(c.get("persona"), c.get("stageId")) for c in commissions}
    escalated: set[str] = set()
    announced = False

    out: list[Decision] = []
    for action in plan.actions:
        if action.type is ActionType.NO_OP:
            out.append(Decision(ActionType.NO_OP, True, reason=(action.reason or "")[:200]))
            continue

        if action.type is ActionType.ISSUE_BOUNTY:
            decision = _decide_issue(action, led, stage_ids, people, seen_keys, budget, open_count)
            out.append(decision)
            if decision.ok:
                seen_keys.add(decision.dedupe)
                budget -= 1
                open_count += 1
            continue

        if action.type is ActionType.ESCALATE_BOUNTY:
            out.append(_decide_escalate(action, open_bounties, escalated))
            if out[-1].ok:
                escalated.add(str(out[-1].bounty_id))
            continue

        if action.type is ActionType.PROPOSE_STAGE_ADVANCE:
            out.append(_decide_advance(action, led, stage_ids, now))
            continue

        if action.type is ActionType.ANNOUNCE:
            message = (action.kioskMessage or "").strip()
            if announced:
                out.append(Decision(action.type, False, "only one announcement per tick"))
            elif not ANNOUNCE_LIMITS[0] <= len(message) <= ANNOUNCE_LIMITS[1]:
                out.append(
                    Decision(
                        action.type,
                        False,
                        f"message must be {ANNOUNCE_LIMITS[0]}-{ANNOUNCE_LIMITS[1]} characters",
                    )
                )
            else:
                announced = True
                out.append(Decision(action.type, True, message=message))
            continue

        if action.type is ActionType.COMMISSION_REEL:
            out.append(_decide_commission(action, stage_ids, seen_commissions))
            if out[-1].ok:
                seen_commissions.add((out[-1].persona, out[-1].stage_id))
            continue

    return out


def _decide_issue(
    action: DirectorAction,
    led: "ledger_mod.Ledger",
    stage_ids: set[str],
    people: dict[str, "ledger_mod.Person"],
    seen_keys: set[str],
    budget: int,
    open_count: int,
) -> Decision:
    if budget <= 0:
        return Decision(
            action.type,
            False,
            f"per-tick bounty budget of {DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK} is spent",
        )
    if open_count >= DIRECTOR_MAX_ACTIVE_BOUNTIES:
        return Decision(
            action.type,
            False,
            f"{open_count} bounties already open (max {DIRECTOR_MAX_ACTIVE_BOUNTIES})",
        )

    stage = (action.targetStage or led.active_stage_id or "").strip() or None
    if stage is not None and stage not in stage_ids:
        return Decision(action.type, False, f"targetStage {stage!r} is not a stage of this event")

    moment = (action.targetMoment or "").strip() or None
    person_id = (action.targetVip or "").strip() or None
    if person_id is not None and person_id not in people:
        # The prompt lists the permitted personIds explicitly. Anything else is a hallucination or a
        # display name, and both would point a bounty at nobody.
        return Decision(
            action.type, False, f"targetVip {person_id!r} is not a person the host promoted"
        )
    if moment is None and person_id is None:
        return Decision(action.type, False, "a bounty must name a targetMoment or a targetVip")

    key = dedupe_key(stage, moment, person_id)
    if key in seen_keys:
        return Decision(action.type, False, f"a bounty for {key} is already open")

    title = (action.title or "").strip()
    copy = (action.guestFacingCopy or "").strip()
    if not TITLE_LIMITS[0] <= len(title) <= TITLE_LIMITS[1]:
        return Decision(action.type, False, f"title must be {TITLE_LIMITS[0]}-{TITLE_LIMITS[1]} characters")
    if not COPY_LIMITS[0] <= len(copy) <= COPY_LIMITS[1]:
        return Decision(
            action.type, False, f"guestFacingCopy must be {COPY_LIMITS[0]}-{COPY_LIMITS[1]} characters"
        )

    person = people.get(person_id) if person_id else None
    weight = person.weight if person is not None else 1.0
    base = int(action.basePoints or BOUNTY_DEFAULT_BASE_POINTS)
    return Decision(
        action.type,
        True,
        stage=stage,
        moment=moment,
        person=person,
        title=title,
        copy=copy,
        base_points=max(1, min(BOUNTY_POINTS_MAX, base)),
        points=points_for(base, weight),
        ttl_minutes=ttl_for(action.expiresInMin),
        audience=action.audience or BountyAudience.ALL,
        dedupe=key,
    )


def _decide_escalate(
    action: DirectorAction,
    open_bounties: dict[str, "ledger_mod.BountyView"],
    already: set[str],
) -> Decision:
    bounty_id = (action.bountyId or "").strip()
    bounty = open_bounties.get(bounty_id)
    if bounty is None:
        return Decision(
            action.type, False, f"{bounty_id!r} is not an open bounty of this event"
        )
    if bounty_id in already or bounty.status == BountyStatus.ESCALATED.value:
        return Decision(action.type, False, f"{bounty_id} is already escalated")
    raised = max(
        BOUNTY_POINTS_MIN,
        min(BOUNTY_POINTS_MAX, int(round((bounty.points or BOUNTY_DEFAULT_BASE_POINTS) * ESCALATION_MULTIPLIER))),
    )
    return Decision(action.type, True, bounty_id=bounty_id, escalated_points=raised)


def _advance_window_minutes(led: "ledger_mod.Ledger", target: "ledger_mod.StageView") -> float:
    """The schedule half of the advance rule, sized to the schedule's own grain (spec 05 §1, spec 13).

    `max(45, 0.25 × minutes to the nearest neighbouring stage)`: a wedding scheduled in 30-minute
    beats keeps the spec's literal ±45, while a trip whose segments sit four hours apart gets ±60 —
    a timetable that loose was never a ±45-minute instrument, and holding it to one turns every
    honest advance into a suggestion card.
    """
    if target.starts_at is None:
        return float(STAGE_ADVANCE_WINDOW_MINUTES)
    neighbours = [
        abs((target.starts_at - s.starts_at).total_seconds()) / 60.0
        for s in led.stages
        if s.stage_id != target.stage_id and s.starts_at is not None
    ]
    if not neighbours:
        return float(STAGE_ADVANCE_WINDOW_MINUTES)
    return max(float(STAGE_ADVANCE_WINDOW_MINUTES), 0.25 * min(neighbours))


def _decide_advance(
    action: DirectorAction,
    led: "ledger_mod.Ledger",
    stage_ids: set[str],
    now: dt.datetime,
) -> Decision:
    """Spec 05 §1's stage guardrail, with spec 13's evidence path beside it.

    Confidence is always required, and then **either** leg suffices: the *schedule* leg (the target's
    window agrees with now, within `_advance_window_minutes`) or the *evidence* leg (the drift signal
    has named this same target for `DRIFT_ADVANCE_TICKS` consecutive ticks — the photos themselves
    say the event has moved, and the photos are the ground truth the schedule only anticipates). One
    tick's drift is deliberately not enough: a burst of forwarded photos from this morning can win a
    single tick, but it cannot keep winning against fresh uploads. An accepted decision with
    `auto_apply=False` is a host-console suggestion card, which is a good outcome and not a failure —
    and while the host holds the stage manually, a suggestion is the only thing the director may ever
    produce (spec 05 §2: "host override always wins instantly").
    """
    target = (action.toStageId or "").strip()
    if target not in stage_ids:
        return Decision(action.type, False, f"toStageId {target!r} is not a stage of this event")
    if target == led.active_stage_id:
        return Decision(action.type, False, f"{target} is already the active stage")

    stage = next((s for s in led.stages if s.stage_id == target), None)
    confidence = float(action.confidence or 0.0)
    window_ok = False
    window_minutes = float(STAGE_ADVANCE_WINDOW_MINUTES)
    if stage is not None and stage.starts_at is not None:
        window_minutes = _advance_window_minutes(led, stage)
        window_ok = abs((now - stage.starts_at).total_seconds()) / 60.0 <= window_minutes
    evidence_ok = (
        led.drift.signal
        and led.drift.top_stage_id == target
        and led.drift_streak >= DRIFT_ADVANCE_TICKS
    )

    if led.active_source == "override":
        why = "the host is holding the stage manually"
    elif confidence < STAGE_ADVANCE_MIN_CONFIDENCE:
        why = f"confidence {confidence:.2f} < {STAGE_ADVANCE_MIN_CONFIDENCE}"
    elif not (window_ok or evidence_ok):
        why = (
            f"the schedule puts {target} more than {window_minutes:.0f} min from now, and the drift "
            f"signal has not named it {DRIFT_ADVANCE_TICKS} ticks running"
        )
    else:
        why = ""

    return Decision(
        action.type,
        True,
        reason=why,
        to_stage=target,
        confidence=confidence,
        auto_apply=not why,
        evidence=(action.evidence or "")[:400],
    )


def _decide_commission(
    action: DirectorAction, stage_ids: set[str], already: set[tuple[Any, Any]]
) -> Decision:
    persona = (action.persona or "").strip().lower()
    if persona not in REEL_PERSONAS:
        return Decision(action.type, False, f"persona {persona!r} is not one of the spec 06 personas")
    stage_id = (action.stageId or "").strip() or None
    if stage_id is not None and stage_id not in stage_ids:
        return Decision(action.type, False, f"stageId {stage_id!r} is not a stage of this event")
    if (persona, stage_id) in already:
        return Decision(action.type, False, f"{persona}/{stage_id} is already commissioned")
    return Decision(
        action.type, True, persona=persona, stage_id=stage_id, reason=(action.reason or "")[:200]
    )


# ---------------------------------------------------------------- outcome


@dataclass
class Outcome:
    """What one tick actually did — the tick's report, the session's record and the log line."""

    issued: list[dict[str, Any]] = field(default_factory=list)
    armed: list[dict[str, Any]] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    advanced: str | None = None
    proposed: str | None = None
    announced: str | None = None
    commissioned: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def action_labels(self) -> list[str]:
        labels = [f"ISSUE:{b['targetLabel']}" for b in self.issued]
        labels += [f"ARM:{b['targetLabel']}" for b in self.armed]
        labels += [f"ESCALATE:{b}" for b in self.escalated]
        labels += [f"EXPIRE:{b}" for b in self.expired]
        if self.advanced:
            labels.append(f"ADVANCE:{self.advanced}")
        if self.proposed:
            labels.append(f"PROPOSE:{self.proposed}")
        if self.announced:
            labels.append("ANNOUNCE")
        labels += [f"COMMISSION:{c['persona']}" for c in self.commissioned]
        return labels

    def as_report(self) -> dict[str, Any]:
        return {
            "issued": [b["bountyId"] for b in self.issued],
            "armed": [b["bountyId"] for b in self.armed],
            "escalated": self.escalated,
            "expired": self.expired,
            "advancedTo": self.advanced,
            "proposedStage": self.proposed,
            "announced": self.announced,
            # `commissioned`, never `rendered`: the tick starts a render job and does not wait for it.
            # A reel takes two to five minutes; the tick is thirty seconds. The `reels/{reelId}` document
            # is where the outcome lands (spec 06 §1), and the kiosk premieres it off the publisher.
            "commissioned": [c["persona"] for c in self.commissioned],
            "rejected": self.rejected,
        }


# ---------------------------------------------------------------- expiry


def expire_bounties(
    event_id: str, now: dt.datetime | None = None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Close out anything past `expiresAt`. Returns (bountyIds, permanent gap records).

    An expired bounty is not a failure to hide. Spec 05 §3: "expired → recorded in ledger as a
    permanent coverage gap (the wrap-up report tells the host honestly)". The record is what lets the
    wrap report say "we never got a photograph of the vidaai, and here is when we asked".
    """
    moment = now or dt.datetime.now(dt.timezone.utc)
    closed: list[str] = []
    gaps: list[dict[str, Any]] = []

    for snap in fs.bounties_col(event_id).stream():
        doc = snap.to_dict() or {}
        if str(doc.get("status") or "") not in OPEN_STATUSES:
            continue
        expires = doc.get("expiresAt")
        if not isinstance(expires, dt.datetime) or expires > moment:
            continue
        snap.reference.update(
            {"status": BountyStatus.EXPIRED.value, "expiredAt": fs.SERVER_TIMESTAMP}
        )
        closed.append(snap.id)
        gaps.append(
            {
                "bountyId": snap.id,
                "dedupeKey": doc.get("dedupeKey"),
                "title": doc.get("title"),
                "targetStage": doc.get("targetStage"),
                "targetMoment": doc.get("targetMoment"),
                "targetVip": doc.get("targetVip"),
                "points": doc.get("points"),
                "submissions": len(doc.get("submissions") or []),
                "expiredAt": moment,
            }
        )
        log.line("bounty", event_id=event_id, bounty=snap.id, outcome="expired")
    return closed, gaps


# ---------------------------------------------------------------- lapse archiving (spec 13)


def archive_lapsed_stages(
    led: "ledger_mod.Ledger", state: session_mod.DirectorState
) -> tuple[list[str], list[dict[str, Any]]]:
    """Fold each newly-lapsed stage's uncovered moments into permanent-gap records, exactly once.

    The other half of the ledger's grace cutoff: `_gaps` stops *reporting* a lapsed stage so Day 1
    cannot crowd Day 4 out of the prompt, and this step makes sure what it stops reporting is not
    forgotten — the records land beside the expired-bounty ones in `directorState.permanentGaps`,
    which is what the wrap report reads to be honest with the host. Pure (no I/O): the session
    record's writer persists both halves of the return value.
    """
    already = set(state.archived_stage_ids)
    archived: list[str] = []
    gaps: list[dict[str, Any]] = []
    for stage in led.stages:
        if stage.stage_id in already or not stage.has_lapsed(led.now):
            continue
        archived.append(stage.stage_id)  # covered stages archive too — "checked" is worth remembering
        for moment_id, label, _tier_weight in stage.required_moments:
            count = stage.moment_counts.get(moment_id, 0)
            if count >= ledger_mod.MOMENT_TARGET_PHOTOS:
                continue
            gaps.append(
                {
                    "kind": "lapsed_stage",
                    "targetStage": stage.stage_id,
                    "stageLabel": stage.label,
                    "targetMoment": moment_id,
                    "title": label,
                    "photos": count,
                    "lapsedAt": led.now,
                }
            )
    if archived:
        log.line(
            "director_lapsed",
            event_id=led.event_id,
            stages=",".join(archived),
            uncovered=len(gaps),
        )
    return archived, gaps


# ---------------------------------------------------------------- arming (spec 05 §2)


def arm_stage_moments(
    event_id: str,
    led: "ledger_mod.Ledger",
    state: session_mod.DirectorState,
    *,
    tick_id: str,
    budget: int = DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK,
) -> list[dict[str, Any]]:
    """Fire the new stage's required-moment bounties the instant the stage becomes active.

    Only on a *transition*: `state.last_stage_id` is what the director already armed, so a stage that
    has been active for twenty ticks arms nothing. The stage is recorded the moment the arming succeeds
    rather than at the end of the tick, because a crash between the two would otherwise arm the same
    moments twice — the dedupe key would catch it, but relying on the second guard to do the first
    guard's job is how a duplicate eventually gets through.
    """
    stage_id = led.active_stage_id
    if not stage_id or stage_id == state.last_stage_id:
        return []

    stage = next((s for s in led.stages if s.stage_id == stage_id), None)
    if stage is None:
        session_mod.note_stage(event_id, stage_id)
        return []

    existing = open_dedupe_keys(event_id)
    open_count = len(existing)
    armed: list[dict[str, Any]] = []
    for moment_id, label, tier_weight in stage.required_moments:
        if len(armed) >= budget or open_count >= DIRECTOR_MAX_ACTIVE_BOUNTIES:
            break
        key = dedupe_key(stage_id, moment_id, None)
        if key in existing:
            continue
        armed.append(
            _write_bounty(
                event_id,
                tick_id=tick_id,
                target_stage=stage_id,
                target_moment=moment_id,
                person=None,
                title=label,
                copy=_ARMED_COPY.format(label=label),
                # `tierWeight` is the required moment's own importance (spec 11 §2's
                # `requiredMomentsTemplate`), which is what makes a kanyadaan bounty outpay a
                # detail shot without any model deciding so.
                base_points=int(round(BOUNTY_DEFAULT_BASE_POINTS * max(0.5, min(2.0, tier_weight)))),
                vip_weight=1.0,
                ttl_minutes=BOUNTY_DEFAULT_TTL_MINUTES,
                audience=BountyAudience.NEAR_STAGE,
                source="armed",
            )
        )
        existing.add(key)
        open_count += 1

    session_mod.note_stage(event_id, stage_id)
    if armed:
        log.line(
            "director_armed",
            event_id=event_id,
            stage=stage_id,
            bounties=len(armed),
            moments=",".join(str(a["targetLabel"]) for a in armed),
        )
    return armed


# ---------------------------------------------------------------- the writer


def apply(
    event_id: str,
    led: "ledger_mod.Ledger",
    plan: DirectorPlan | None,
    *,
    tick_id: str,
    outcome: Outcome,
    state: session_mod.DirectorState,
    now: dt.datetime | None = None,
) -> Outcome:
    """Run `decide`, then execute what it accepted. The only writer of a bounty document."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    if plan is None:
        return outcome

    decisions = decide(
        led,
        plan,
        # Both counts are read from the ledger rather than reconstructed here. `expire_bounties` runs
        # *before* the ledger is built, so its closures are already absent from `open_bounty_count`;
        # `arm_stage_moments` runs after, so `director.py` folds its result in — adjusting for either
        # again here is how an off-by-two guardrail happens (it did, before this comment existed).
        open_keys=open_dedupe_keys(event_id),
        open_count=max(0, led.open_bounty_count),
        budget=max(0, DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK - len(outcome.armed)),
        commissions=state.commissions,
        now=moment,
    )

    for decision in decisions:
        if not decision.ok:
            outcome.rejected.append(f"{decision.type.value}: {decision.reason}")
            continue
        try:
            _execute(event_id, decision, tick_id=tick_id, outcome=outcome)
        except Exception as exc:  # noqa: BLE001 - one bad action must not lose the rest of the plan
            log.error(
                "director_action_failed",
                event_id=event_id,
                tick_id=tick_id,
                action=decision.type.value,
                err=str(exc),
            )
            outcome.rejected.append(f"{decision.type.value}: {exc}")

    if outcome.rejected:
        # Spec 05 §5: rejected actions are logged, never silently dropped — a model whose plans keep
        # being thrown away is a prompt that needs fixing, and the only way to know is a record.
        log.line(
            "director_rejected",
            severity="WARNING",
            event_id=event_id,
            tick_id=tick_id,
            count=len(outcome.rejected),
            detail=" | ".join(outcome.rejected)[:400],
        )
    return outcome


def _execute(event_id: str, decision: Decision, *, tick_id: str, outcome: Outcome) -> None:
    kind = decision.type

    if kind is ActionType.NO_OP:
        return

    if kind is ActionType.ISSUE_BOUNTY:
        outcome.issued.append(
            _write_bounty(
                event_id,
                tick_id=tick_id,
                target_stage=decision.stage,
                target_moment=decision.moment,
                person=decision.person,
                title=decision.title,
                copy=decision.copy,
                base_points=decision.base_points,
                vip_weight=decision.person.weight if decision.person else 1.0,
                ttl_minutes=decision.ttl_minutes,
                audience=decision.audience,
                source="reconciliation",
            )
        )
        return

    if kind is ActionType.ESCALATE_BOUNTY:
        # `kioskTakeover` is what the publisher already keys the full-screen `bounty_call` slot off
        # (spec 04 §4, `publisher/store.py::takeover_bounty`) — the contract S8a wrote for this session.
        fs.bounty_ref(event_id, str(decision.bounty_id)).update(
            {
                "status": BountyStatus.ESCALATED.value,
                "points": decision.escalated_points,
                "audience": BountyAudience.ALL.value,
                "kioskTakeover": True,
                "escalatedAt": fs.SERVER_TIMESTAMP,
            }
        )
        outcome.escalated.append(str(decision.bounty_id))
        log.line(
            "bounty",
            event_id=event_id,
            bounty=decision.bounty_id,
            outcome="escalated",
            points=decision.escalated_points,
        )
        return

    if kind is ActionType.PROPOSE_STAGE_ADVANCE:
        if decision.auto_apply:
            fs.event_ref(event_id).set(
                {
                    "activeStage": decision.to_stage,
                    "stageAdvancedAt": fs.SERVER_TIMESTAMP,
                    "stageAdvancedBy": "story_director",
                    "stageAdvanceConfidence": decision.confidence,
                },
                merge=True,
            )
            outcome.advanced = decision.to_stage
            log.line(
                "director_stage",
                event_id=event_id,
                outcome="advanced",
                stage=decision.to_stage,
                confidence=round(decision.confidence, 2),
            )
            return
        # A host-console suggestion card, as an unresolved `ops/` alert — the surface the console
        # already reads for its badge, so no new collection and no new security rule.
        fs.ops_alert(
            event_id,
            "stage_suggestion",
            f"the director thinks the event has moved to {decision.to_stage} ({decision.reason})",
            severity="info",
            toStageId=decision.to_stage,
            confidence=decision.confidence,
            evidence=decision.evidence,
            proposedBy="story_director",
        )
        outcome.proposed = decision.to_stage
        log.line(
            "director_stage",
            event_id=event_id,
            outcome="proposed",
            stage=decision.to_stage,
            confidence=round(decision.confidence, 2),
            why=decision.reason,
        )
        return

    if kind is ActionType.ANNOUNCE:
        fs.event_ref(event_id).set(
            {
                "announcement": {
                    "message": decision.message,
                    "at": fs.SERVER_TIMESTAMP,
                    "tickId": tick_id,
                }
            },
            merge=True,
        )
        outcome.announced = decision.message
        return

    if kind is ActionType.COMMISSION_REEL:
        # Executed as of B4-S11: `directors/reel/commission.py` creates the `reels/` document and starts
        # the render job, and it re-checks every guardrail (event status, the panic freeze, one in-flight
        # commission per persona, the daily ceiling) rather than trusting this call site — the same
        # discipline the bounty path follows. A refusal is *recorded* on the director state anyway, so a
        # commission the director asked for is never silently lost: the wrap report can say the film was
        # wanted and why it did not happen.
        from directors.reel import commission as reel_commission
        from schemas.reel import ReelPersona

        result = reel_commission.commission(
            event_id,
            persona=ReelPersona(decision.persona),
            stage_id=decision.stage_id,
            reason=decision.reason,
            commissioned_by="director",
        )
        outcome.commissioned.append(
            session_mod.remember_commission(
                {
                    "persona": decision.persona,
                    "stageId": decision.stage_id,
                    "reason": decision.reason,
                    "status": "producing" if result.ok else "refused",
                    "reelId": result.reel_id,
                    "note": result.reason,
                }
            )
        )
        if not result.ok:
            outcome.rejected.append(f"COMMISSION_REEL:{decision.persona}: {result.reason}")
        return


def _write_bounty(
    event_id: str,
    *,
    tick_id: str,
    target_stage: str | None,
    target_moment: str | None,
    person: "ledger_mod.Person | None",
    title: str,
    copy: str,
    base_points: int,
    vip_weight: float,
    ttl_minutes: int,
    audience: BountyAudience,
    source: str,
) -> dict[str, Any]:
    """Write one bounty document. Points are scaled and clamped by `points_for`, exactly once."""
    now = dt.datetime.now(dt.timezone.utc)
    points = points_for(base_points, vip_weight)
    ttl = ttl_for(ttl_minutes)
    bounty_id = new_ulid()

    bounty = Bounty(
        bountyId=bounty_id,
        status=BountyStatus.ACTIVE,
        targetStage=target_stage,
        targetMoment=target_moment,
        targetVip=person.person_id if person else None,
        targetVipName=person.display_name if person else None,
        dedupeKey=dedupe_key(target_stage, target_moment, person.person_id if person else None),
        title=title,
        guestCopy=copy,
        points=points,
        basePoints=max(1, min(BOUNTY_POINTS_MAX, int(base_points))),
        vipWeight=vip_weight,
        audience=audience,
        source=source,
        tickId=tick_id,
        expiresAt=now + dt.timedelta(minutes=ttl),
    )
    # `by_alias` so the document carries spec 05 §3's `copy`, which is what the PWA banner and the
    # kiosk poster read — the model's own field name never reaches Firestore.
    payload = bounty.model_dump(mode="json", by_alias=True, exclude={"createdAt", "expiresAt"})
    payload["createdAt"] = fs.SERVER_TIMESTAMP
    payload["expiresAt"] = bounty.expiresAt
    fs.bounty_ref(event_id, bounty_id).set(payload)

    log.line(
        "bounty",
        event_id=event_id,
        bounty=bounty_id,
        outcome="issued",
        source=source,
        stage=target_stage,
        moment=target_moment,
        vip=person.person_id if person else None,
        points=points,
        base=base_points,
        weight=vip_weight,
        ttl_min=ttl,
    )
    return {
        "bountyId": bounty_id,
        "dedupeKey": bounty.dedupeKey,
        "points": points,
        "targetLabel": target_moment or (person.display_name if person else target_stage or "?"),
        "source": source,
    }


def open_dedupe_keys(event_id: str) -> set[str]:
    """The (stage, moment, vip) keys currently spoken for. Read fresh every time: the guardrail has to
    hold across ticks and restarts, not merely within one plan."""
    keys: set[str] = set()
    for snap in fs.bounties_col(event_id).stream():
        doc = snap.to_dict() or {}
        if str(doc.get("status") or "") in OPEN_STATUSES:
            keys.add(
                str(
                    doc.get("dedupeKey")
                    or dedupe_key(
                        doc.get("targetStage"), doc.get("targetMoment"), doc.get("targetVip")
                    )
                )
            )
    return keys
