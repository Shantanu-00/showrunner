"""LEDGER — spec 05 §1's first step, and the reason the director is not a prompt with a database.

Everything the REASON step is allowed to know is computed here, deterministically, with no model
anywhere near it. That ordering is the design: a language model handed raw Firestore would be
reasoning about photographs; a language model handed *this* is reasoning about an event. The
difference shows up in the actions.

Four properties worth checking against the code:

- **O(1) in event size.** Six reads regardless of whether the event has 20 photos or 5,000: the
  coverage shards (a handful of small documents, maintained incrementally by `shared/coverage.py`),
  the bounties, the people, one 20-document sample for the drift signal, and two count aggregations.
  Nothing here scans the media collection.
- **Coverage counts evidence, not exposure.** A Ring-0 photo of the groom's mother proves she was
  photographed; issuing a bounty for her because her photo is private would be the system mistaking
  its own consent architecture for a gap. `publicCount` is tracked separately for the questions that
  really are about the wall.
- **The gap list is ordered by tier first, severity second** (spec 05 §1's "tier also orders which gap
  gets acted on, not just how it's paid" and spec 11 §3.3 point 3). Ordering is arithmetic on
  host-declared metadata — the model chooses *whether* to act, never *who matters*.
- **Only future-proof identifiers reach the prompt.** Stage ids, moment ids and personIds are printed
  verbatim precisely so the model can only ever copy them back; `act.py` rejects anything else. There
  is no path by which a hallucinated name becomes a bounty target.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.bounty import OPEN_STATUSES, BountyStatus
from schemas.common import MediaStatus
from shared import coverage, fs, log
from shared.settings import (
    DEFAULT_TIER,
    DRIFT_MIN_VISUAL,
    DRIFT_SAMPLE_SIZE,
    DRIFT_VOTE_FRACTION,
    NEAR_STAGE_WINDOW_MINUTES,
    UPLOAD_VELOCITY_WINDOW_MINUTES,
    VIP_WEIGHT_BY_TIER,
)

from . import session as session_mod

#: A required moment is covered at two photographs. One is luck — a single frame of the varmala can be
#: blurred, half-obscured or of the wrong two people — and a director that stops asking after one is
#: a director that files a coverage report it cannot support. Not spec-pinned (HANDOFF §9).
MOMENT_TARGET_PHOTOS = 2

#: How many gaps reach the prompt. The REASON step may issue at most two bounties per tick, so a list
#: of eight is already four times more choice than it can use; more is prompt weight for nothing.
MAX_GAPS_IN_PROMPT = 8

#: Tiers that are worth a targeted bounty at all: the host-promoted ones (spec 11 §3.1). Every guest
#: sits at tier 3 by default, and "get a photo of an unpromoted guest" is not a coverage goal.
VIP_TIER_CEILING = 2


def vip_weight(tier: int | None) -> float:
    """Spec 11 §3.3's table, verbatim, read from `shared/settings.py` — never a model's opinion."""
    return VIP_WEIGHT_BY_TIER.get(int(tier if tier is not None else DEFAULT_TIER), 1.0)


# ---------------------------------------------------------------- value types


@dataclass(frozen=True)
class Person:
    person_id: str
    display_name: str
    tier: int

    @property
    def weight(self) -> float:
        return vip_weight(self.tier)


@dataclass(frozen=True)
class StageView:
    stage_id: str
    label: str
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    required_moments: tuple[tuple[str, str, float], ...]  # (momentId, label, tierWeight)
    photo_count: int
    public_count: int
    highlight_count: int
    mean_aesthetic: float
    last_captured_at: dt.datetime | None
    moment_counts: dict[str, int]

    def scheduled_now(self, now: dt.datetime) -> bool:
        if self.starts_at is None or self.ends_at is None:
            return False
        return self.starts_at <= now <= self.ends_at

    def has_started(self, now: dt.datetime) -> bool:
        return self.starts_at is None or self.starts_at <= now


@dataclass(frozen=True)
class Gap:
    """One thing the event is missing. `severity` orders within a tier, never across tiers."""

    kind: str  # 'moment' | 'vip'
    stage_id: str
    stage_label: str
    label: str
    severity: float
    vip_weight: float
    photo_count: int
    moment_id: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    tier: int | None = None

    @property
    def sort_key(self) -> tuple[float, float]:
        return (self.vip_weight, self.severity)

    def as_line(self) -> str:
        who = f" person={self.person_id} ({self.person_name}, tier {self.tier})" if self.person_id else ""
        what = f" moment={self.moment_id}" if self.moment_id else ""
        return (
            f"- stage={self.stage_id}{what}{who} :: {self.label}; photos so far {self.photo_count}; "
            f"vipWeight {self.vip_weight:.1f}; severity {self.severity:.2f}"
        )


@dataclass(frozen=True)
class BountyView:
    bounty_id: str
    status: str
    title: str
    target: str
    points: int
    age_minutes: float
    ttl_minutes: float
    past_half_life: bool
    submissions: int

    def as_line(self) -> str:
        flag = " PAST-HALF-LIFE" if self.past_half_life and self.status == BountyStatus.ACTIVE.value else ""
        return (
            f"- id={self.bounty_id} status={self.status} points={self.points} "
            f"age={self.age_minutes:.0f}m of {self.ttl_minutes:.0f}m submissions={self.submissions}"
            f"{flag} :: {self.title} [{self.target}]"
        )


@dataclass(frozen=True)
class Drift:
    """Spec 05 §2's visual-vs-schedule disagreement, computed from stored Curator distributions."""

    sample: int
    votes: int
    top_stage_id: str | None
    signal: bool

    def as_line(self) -> str:
        if not self.sample:
            return "no recent photos to compare against the schedule"
        if not self.top_stage_id:
            return f"{self.sample} recent photos agree with the active stage"
        verdict = "SIGNAL" if self.signal else "weak"
        return (
            f"{self.votes} of the last {self.sample} photos look like stage "
            f"{self.top_stage_id} rather than the active one ({verdict})"
        )


@dataclass
class Ledger:
    event_id: str
    event_name: str
    now: dt.datetime
    status: str
    active_stage_id: str | None
    active_stage_label: str
    active_source: str  # override | schedule | none
    scheduled_stage_id: str | None
    stages: list[StageView] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    bounties: list[BountyView] = field(default_factory=list)
    open_bounty_count: int = 0
    drift: Drift = field(default_factory=lambda: Drift(0, 0, None, False))
    velocity: int = 0
    active_guests: int = 0
    glossary: list[str] = field(default_factory=list)
    host_preferences: str = ""
    #: The distilled venue paragraph (`directors/story/world.py`), or "". Advisory prose, exactly like
    #: `host_preferences` below it in the prompt — it tells the director *where* things happen so a
    #: bounty can say where to go, and it is not a source of identifiers.
    world_model: str = ""
    #: `stageId → dominant observed setting`. Counts, not prose, so this half is safe to reason from.
    stage_settings: dict[str, str] = field(default_factory=dict)
    narrative: str = "(this is the first tick of this event)"
    #: Filled in after the build, by the deterministic arming step that runs between LEDGER and REASON.
    #: The model has to be told, because otherwise it spends its whole plan proposing bounties the
    #: timetable already fired and the guardrails then reject every one — measured live on the first
    #: real tick, where it also produced an assessment claiming credit for them.
    armed_this_tick: list[str] = field(default_factory=list)
    bounty_budget: int = 0

    # ------------------------------------------------------------------ prompt

    def as_prompt_block(self) -> str:
        """The whole of what the model is told. Deliberately short and entirely identifiers + counts."""
        lines: list[str] = [
            "--- EVENT ---",
            f"event={self.event_id} name={self.event_name!r} status={self.status} "
            f"localTime={self.now:%H:%M} (UTC)",
            f"activeStage={self.active_stage_id or 'none'} ({self.active_stage_label}) "
            f"source={self.active_source}; scheduleSaysNow={self.scheduled_stage_id or 'none'}",
            f"uploadsLast{UPLOAD_VELOCITY_WINDOW_MINUTES}min={self.velocity} activeGuests={self.active_guests}",
        ]
        if self.glossary:
            lines.append(f"culturalGlossary={', '.join(self.glossary)}")

        lines.append("")
        lines.append("--- TIMELINE AND COVERAGE ---")
        for stage in self.stages:
            window = _window_text(stage)
            moments = ", ".join(
                f"{mid}:{stage.moment_counts.get(mid, 0)}" for mid, _, _ in stage.required_moments
            )
            marker = ">>" if stage.stage_id == self.active_stage_id else "  "
            lines.append(
                f"{marker} {stage.stage_id} ({stage.label}) {window} photos={stage.photo_count} "
                f"public={stage.public_count} good={stage.highlight_count} "
                f"meanAesthetic={stage.mean_aesthetic:.2f}"
                + (f" requiredMoments[{moments}]" if moments else "")
            )
        if not self.stages:
            lines.append("  (this event has no schedule — no stage may be advanced or targeted)")

        lines.append("")
        lines.append(f"--- STAGE DRIFT --- {self.drift.as_line()}")

        lines.append("")
        lines.append("--- PEOPLE THE HOST NAMED (the only permitted targetVip values) ---")
        if self.people:
            for person in self.people:
                lines.append(
                    f"- {person.person_id} {person.display_name!r} tier={person.tier} "
                    f"vipWeight={person.weight:.1f}"
                )
        else:
            lines.append("- (none: the host has promoted nobody, so no bounty may name a person)")

        lines.append("")
        lines.append(f"--- COVERAGE GAPS (ranked, tier first) --- {len(self.gaps)} found")
        for gap in self.gaps[:MAX_GAPS_IN_PROMPT]:
            lines.append(gap.as_line())
        if not self.gaps:
            lines.append("- none: every started stage has its required moments and named people covered")

        lines.append("")
        lines.append(f"--- BOUNTIES --- {self.open_bounty_count} open")
        for bounty in self.bounties:
            lines.append(bounty.as_line())
        if not self.bounties:
            lines.append("- none")
        if self.armed_this_tick:
            lines.append(
                f"Already fired this tick from the timetable, before you were asked: "
                f"{', '.join(self.armed_this_tick)}. Do not ask for these again."
            )
        lines.append(
            f"You may issue at most {self.bounty_budget} new bounties on this tick."
            + (" Issue none; use NO_OP." if self.bounty_budget <= 0 else "")
        )

        # Deliberately placed here: after every block that establishes *identifiers* the model is
        # licensed to copy (stageIds, momentIds, personIds, bountyIds) and beside the other advisory
        # prose block. Venue nouns sitting among the identifier lists would invite the model to emit
        # "the lawn" as a targetStage, which `act.py` would reject — a wasted tick.
        if self.world_model or self.stage_settings:
            lines.append("")
            lines.append("--- PHYSICAL SETTING (advisory only — never an identifier) ---")
            if self.stage_settings:
                observed = " ".join(
                    f"{stage_id}={setting}" for stage_id, setting in sorted(self.stage_settings.items())
                )
                lines.append(f"observed by stage: {observed}")
            if self.world_model:
                lines.append(f"model: {self.world_model}")
            lines.append(
                "Use this to say WHERE to go in guestFacingCopy. Never invent a place it does not name."
            )

        if self.host_preferences:
            lines.append("")
            lines.append(f"--- HOST STANDING PREFERENCES (advisory only) --- {self.host_preferences}")

        lines.append("")
        lines.append("--- WHAT I DID ON PREVIOUS TICKS ---")
        lines.append(self.narrative)
        return "\n".join(lines)


def _window_text(stage: StageView) -> str:
    if stage.starts_at is None or stage.ends_at is None:
        return "[unscheduled]"
    return f"[{stage.starts_at:%H:%M}-{stage.ends_at:%H:%M}]"


# ---------------------------------------------------------------- the build


def build(
    event_id: str,
    event: dict[str, Any],
    state: session_mod.DirectorState,
    *,
    host_preferences: str = "",
    world_model: str = "",
    now: dt.datetime | None = None,
) -> Ledger:
    """Aggregate everything the REASON step needs. Six reads, no model, no writes."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    shards = coverage.read(event_id)
    people = _people(event_id)
    stages = _stages(event, shards)

    active_id, active_source = _active_stage(event)
    scheduled_id = next((s.stage_id for s in stages if s.scheduled_now(moment)), None)
    if active_id is None:
        # No override and no `activeStage` written yet: the schedule is the answer, which is also what
        # `GET /v1/events/{id}/public` and the publisher would show.
        active_id, active_source = scheduled_id, "schedule" if scheduled_id else "none"

    active_label = next((s.label for s in stages if s.stage_id == active_id), "unscheduled")
    bounties = _bounties(event_id, moment)

    ledger = Ledger(
        event_id=event_id,
        event_name=str(event.get("name") or "unnamed"),
        now=moment,
        status=str(event.get("status") or "draft"),
        active_stage_id=active_id,
        active_stage_label=active_label,
        active_source=active_source,
        scheduled_stage_id=scheduled_id,
        stages=stages,
        people=people,
        bounties=bounties,
        open_bounty_count=sum(1 for b in bounties if b.status in OPEN_STATUSES),
        drift=_drift(event_id, active_id),
        velocity=_velocity(event_id, moment),
        active_guests=_active_guests(event_id, moment),
        glossary=[str(g) for g in ((event.get("eventTypeProfile") or {}).get("culturalGlossary") or [])],
        host_preferences=host_preferences,
        world_model=world_model,
        # Free: `shards` is already in hand, and `dominant_scene` deliberately ignores the settings
        # that carry no location information, so a stage of nothing but close-ups reports nothing
        # rather than reporting "closeup_detail" as though that were a place.
        stage_settings={
            stage_id: shard.dominant_scene
            for stage_id, shard in shards.items()
            if shard.dominant_scene
        },
        narrative=state.narrative(),
    )
    ledger.gaps = _gaps(ledger, shards, moment)
    return ledger


def _active_stage(event: dict[str, Any]) -> tuple[str | None, str]:
    """The host's override beats the schedule instantly (spec 05 §2), exactly as the publisher reads it."""
    override = event.get("stageOverride")
    if override:
        return str(override), "override"
    active = event.get("activeStage")
    if active:
        return str(active), "activeStage"
    return None, "none"


def _stages(event: dict[str, Any], shards: dict[str, coverage.StageCoverage]) -> list[StageView]:
    out: list[StageView] = []
    for stage in event.get("stages") or []:
        stage_id = str(stage.get("stageId") or "")
        if not stage_id:
            continue
        shard = shards.get(stage_id) or coverage.StageCoverage(stage_id)
        moments = tuple(
            (
                str(m.get("momentId")),
                str(m.get("label") or m.get("momentId")),
                float(m.get("tierWeight") or 1.0),
            )
            for m in (stage.get("requiredMoments") or [])
            if m.get("momentId")
        )
        out.append(
            StageView(
                stage_id=stage_id,
                label=str(stage.get("label") or stage_id),
                starts_at=_as_dt(stage.get("startsAt")),
                ends_at=_as_dt(stage.get("endsAt")),
                required_moments=moments,
                photo_count=shard.photo_count,
                public_count=shard.public_count,
                highlight_count=shard.highlight_count,
                mean_aesthetic=shard.mean_aesthetic,
                last_captured_at=shard.last_captured_at,
                moment_counts=dict(shard.moments),
            )
        )
    return out


def _people(event_id: str) -> list[Person]:
    """Host-promoted people only (tier ≤ 2). Dozens of documents at most, and it is the input to a
    deterministic multiplier, so it is read fresh every tick rather than cached — a promotion the
    director keeps ignoring is exactly what a host would report as a bug."""
    found: list[Person] = []
    for snap in fs.people_col(event_id).stream():
        doc = snap.to_dict() or {}
        try:
            tier = int(doc.get("tier", DEFAULT_TIER))
        except (TypeError, ValueError):
            tier = DEFAULT_TIER
        if tier > VIP_TIER_CEILING:
            continue
        found.append(
            Person(
                person_id=snap.id,
                display_name=str(doc.get("displayName") or "unnamed"),
                tier=tier,
            )
        )
    found.sort(key=lambda p: (p.tier, p.display_name))
    return found


def _bounties(event_id: str, now: dt.datetime) -> list[BountyView]:
    views: list[BountyView] = []
    for snap in fs.bounties_col(event_id).stream():
        doc = snap.to_dict() or {}
        status = str(doc.get("status") or BountyStatus.ACTIVE.value)
        if status not in OPEN_STATUSES:
            continue  # fulfilled/expired history belongs to the wrap report, not to this prompt
        created = _as_dt(doc.get("createdAt"))
        expires = _as_dt(doc.get("expiresAt"))
        age = (now - created).total_seconds() / 60 if created else 0.0
        ttl = (expires - created).total_seconds() / 60 if created and expires else 0.0
        views.append(
            BountyView(
                bounty_id=snap.id,
                status=status,
                title=str(doc.get("title") or "untitled"),
                target=str(doc.get("dedupeKey") or "-"),
                points=int(doc.get("points") or 0),
                age_minutes=age,
                ttl_minutes=ttl,
                past_half_life=bool(ttl and age >= ttl / 2),
                submissions=len(doc.get("submissions") or []),
            )
        )
    views.sort(key=lambda b: -b.age_minutes)
    return views


def _gaps(ledger: Ledger, shards: dict[str, coverage.StageCoverage], now: dt.datetime) -> list[Gap]:
    """Required moments first, then named people in the active stage. Ranked tier-first."""
    gaps: list[Gap] = []

    for stage in ledger.stages:
        if not stage.has_started(now):
            continue  # not a gap yet: the varmala is not missing before the varmala
        for moment_id, label, tier_weight in stage.required_moments:
            count = stage.moment_counts.get(moment_id, 0)
            if count >= MOMENT_TARGET_PHOTOS:
                continue
            severity = (1.0 - count / MOMENT_TARGET_PHOTOS) * max(0.1, min(2.0, tier_weight))
            if stage.stage_id == ledger.active_stage_id:
                severity *= 1.25  # happening now, so it is still fixable
            gaps.append(
                Gap(
                    kind="moment",
                    stage_id=stage.stage_id,
                    stage_label=stage.label,
                    label=f"required moment {label!r} is under-covered",
                    severity=min(1.5, severity),
                    vip_weight=1.0,
                    photo_count=count,
                    moment_id=moment_id,
                )
            )

    active = next((s for s in ledger.stages if s.stage_id == ledger.active_stage_id), None)
    if active is not None:
        appearances = (shards.get(active.stage_id) or coverage.StageCoverage(active.stage_id)).people
        for person in ledger.people:
            count = appearances.get(person.person_id, 0)
            if count >= 1:
                continue
            gaps.append(
                Gap(
                    kind="vip",
                    stage_id=active.stage_id,
                    stage_label=active.label,
                    label=f"{person.display_name} has no photograph in this stage",
                    severity=1.0,
                    vip_weight=person.weight,
                    photo_count=count,
                    person_id=person.person_id,
                    person_name=person.display_name,
                    tier=person.tier,
                )
            )

    gaps.sort(key=lambda g: g.sort_key, reverse=True)
    return gaps


def _drift(event_id: str, active_stage_id: str | None) -> Drift:
    """Spec 05 §2's drift signal, from the raw pre-fusion distributions the Curator already stored.

    The Curator is never told when a photo was taken, so its `visual` scores and the schedule are two
    genuinely independent signals — which is the only reason their disagreement means anything. This
    reads the last `DRIFT_SAMPLE_SIZE` indexed items and counts how many look more like some *other*
    stage than the one the host says is running.
    """
    if not active_stage_id:
        return Drift(0, 0, None, False)
    try:
        query = (
            fs.media_col(event_id)
            .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
            .order_by("uploadedAt", direction=firestore.Query.DESCENDING)
            .limit(DRIFT_SAMPLE_SIZE)
        )
        docs = [snap.to_dict() or {} for snap in query.stream()]
    except Exception as exc:  # noqa: BLE001 - a missing index must not fail a tick
        log.warn("drift_query_failed", event_id=event_id, err=str(exc))
        return Drift(0, 0, None, False)

    votes: dict[str, int] = {}
    sample = 0
    for doc in docs:
        visual = (doc.get("curator") or {}).get("visual") or {}
        if not isinstance(visual, dict) or not visual:
            continue
        sample += 1
        best_stage, best_score = max(visual.items(), key=lambda kv: float(kv[1] or 0.0))
        if best_stage != active_stage_id and float(best_score or 0.0) >= DRIFT_MIN_VISUAL:
            votes[str(best_stage)] = votes.get(str(best_stage), 0) + 1

    if not votes or not sample:
        return Drift(sample, 0, None, False)
    top_stage, top_votes = max(votes.items(), key=lambda kv: kv[1])
    return Drift(sample, top_votes, top_stage, top_votes >= max(2, sample * DRIFT_VOTE_FRACTION))


def _velocity(event_id: str, now: dt.datetime) -> int:
    since = now - dt.timedelta(minutes=UPLOAD_VELOCITY_WINDOW_MINUTES)
    return _count(fs.media_col(event_id).where(filter=FieldFilter("uploadedAt", ">=", since)))


def _active_guests(event_id: str, now: dt.datetime) -> int:
    """Guests seen inside the `nearStage` window — which is also who a `nearStage` bounty reaches
    (spec 05 §4), so the model is told the size of the audience it is choosing between."""
    since = now - dt.timedelta(minutes=NEAR_STAGE_WINDOW_MINUTES)
    return _count(
        fs.event_ref(event_id)
        .collection("guests")
        .where(filter=FieldFilter("lastSeenAt", ">=", since))
    )


def _count(query: Any) -> int:
    """A Firestore count aggregation — billed per 1,000 index entries rather than per document, which
    is what keeps "how busy is this event" from costing a scan."""
    try:
        result = query.count().get()
        return int(result[0][0].value)
    except Exception as exc:  # noqa: BLE001 - a count is telemetry, never a reason to fail a tick
        log.warn("count_failed", err=str(exc))
        return 0


def _as_dt(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return None
