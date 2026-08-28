"""Smoke-test the Story Director (S8b) — the 40%-criterion claim, made falsifiable.

Companion to `smoke_upload.py` (spine + Curator), `smoke_faces.py` (identity), `smoke_safety.py`
(Guardian + indexing) and `smoke_autonomy.py` (Scheduler → tick → wall). Those prove the data plane and
the heartbeat. This proves the *decision*: that something with a goal looked at the event, noticed what
was missing, and asked five hundred strangers for it without anybody pressing anything.

Two halves, deliberately:

  1. `--guardrails-only` — the whole of spec 05 §1's guardrail set and spec 05 §5's fourth acceptance
     criterion ("guardrails hold under adversarial LLM output: invalid actions are rejected and logged,
     never applied") as a decision table, with **no network, no Firestore and no spend**. `act.decide`
     is pure for exactly this reason. Same shape as `smoke_safety.py --gate-only` and
     `smoke_autonomy.py --program-only`: the rules that decide who gets paid should be checkable
     without a cloud account.

  2. the live run — a purpose-built `internal_dev` event with an under-covered active stage and one
     tier-0 Principal with no photographs, ticked through the real `POST /internal/tick` on the real
     `api` service. It asserts spec 05 §5's other criteria against real documents: a bounty appears
     within one tick; a submission is validated and awarded exactly once; two quiet ticks issue nothing
     new; an expired bounty is recorded as a permanent coverage gap; the coverage counters moved inside
     the same transaction that indexed a photo.

    python scripts/smoke_director.py --guardrails-only         # offline, free
    python scripts/smoke_director.py --api https://api-...run.app
    python scripts/smoke_director.py --keep                    # leave the event live for inspection
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from directors.story import act, ledger as ledger_mod, session as session_mod  # noqa: E402
from schemas.bounty import OPEN_STATUSES, BountyAudience, BountyStatus  # noqa: E402
from schemas.director import ActionType, DirectorAction, DirectorPlan  # noqa: E402
from schemas.event import EventClass, EventStatus  # noqa: E402
from shared import coverage, fs  # noqa: E402
from shared.settings import (  # noqa: E402
    BOUNTY_POINTS_MAX,
    BOUNTY_POINTS_MIN,
    DIRECTOR_MAX_ACTIVE_BOUNTIES,
    DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK,
    DIRECTOR_SESSION_WINDOW,
    settings,
)
from shared.ulid import new_ulid  # noqa: E402

from dev_event import build_stages, firestore_ready  # noqa: E402
from smoke_faces import mint_host_token  # noqa: E402
from smoke_upload import put_bytes, sign_in_anonymously, unique_jpeg  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: The AI-generated cast portrait from the B1 probes — a real face, no real guest (video content
#: rules). It has to be a real photograph rather than a gradient: a gradient scores 0.0 through the
#: Curator and never reaches a public surface, so it would test the aesthetic floor instead of the
#: director.
PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"

#: A stable id, so a re-run reuses one event instead of leaving a trail of live ones behind (each live
#: event costs a director call on every production tick — HANDOFF §9's housekeeping note).
EVENT_ID = "dev_director"


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ================================================================ 1. the guardrails, offline


def _person(person_id: str, tier: int, name: str) -> ledger_mod.Person:
    return ledger_mod.Person(person_id=person_id, display_name=name, tier=tier)


def _stage(
    stage_id: str, *, starts: dt.datetime | None, moments: tuple[tuple[str, str, float], ...] = ()
) -> ledger_mod.StageView:
    return ledger_mod.StageView(
        stage_id=stage_id,
        label=stage_id.title(),
        starts_at=starts,
        ends_at=(starts + dt.timedelta(hours=2)) if starts else None,
        required_moments=moments,
        photo_count=0,
        public_count=0,
        highlight_count=0,
        mean_aesthetic=0.0,
        last_captured_at=None,
        moment_counts={},
    )


def _bounty_view(bounty_id: str, *, status: str, points: int, age: float, ttl: float) -> ledger_mod.BountyView:
    return ledger_mod.BountyView(
        bounty_id=bounty_id,
        status=status,
        title="t",
        target="sangeet|first_dance|-",
        points=points,
        age_minutes=age,
        ttl_minutes=ttl,
        past_half_life=age >= ttl / 2 if ttl else False,
        submissions=0,
    )


def _ledger(now: dt.datetime, *, active_source: str = "activeStage") -> ledger_mod.Ledger:
    return ledger_mod.Ledger(
        event_id="e",
        event_name="Test",
        now=now,
        status="live",
        active_stage_id="sangeet",
        active_stage_label="Sangeet",
        active_source=active_source,
        scheduled_stage_id="sangeet",
        stages=[
            _stage("haldi", starts=now - dt.timedelta(hours=4)),
            _stage(
                "sangeet",
                starts=now - dt.timedelta(minutes=30),
                moments=(("first_dance", "First dance", 1.5),),
            ),
            _stage("ceremony", starts=now + dt.timedelta(minutes=20)),
            _stage("vidaai", starts=now + dt.timedelta(hours=6)),
        ],
        people=[_person("P0", 0, "Aarav"), _person("P3", 3, "A guest")],
        bounties=[_bounty_view("B_OPEN", status="active", points=200, age=15, ttl=20)],
        open_bounty_count=1,
    )


def _action(kind: ActionType, **fields: Any) -> DirectorAction:
    return DirectorAction(type=kind, **fields)


def _valid_issue(**overrides: Any) -> DirectorAction:
    base: dict[str, Any] = {
        "targetStage": "sangeet",
        "targetMoment": "first_dance",
        "title": "The first dance",
        "guestFacingCopy": "They are about to take the floor — get a shot from the front.",
        "basePoints": 100,
        "expiresInMin": 20,
        "audience": BountyAudience.NEAR_STAGE,
    }
    base.update(overrides)
    return _action(ActionType.ISSUE_BOUNTY, **base)


def check_guardrails() -> None:
    """Every row is a sentence from spec 05 §1 or §5. `act.decide` is pure, so this costs nothing."""
    now = dt.datetime.now(dt.timezone.utc)
    led = _ledger(now)
    open_keys = {"sangeet|first_dance|-"}

    def run(
        actions: list[DirectorAction],
        *,
        keys: set[str] | None = None,
        open_count: int = 1,
        budget: int = DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK,
        commissions: list[dict[str, Any]] | None = None,
        ledger: ledger_mod.Ledger | None = None,
    ) -> list[act.Decision]:
        return act.decide(
            ledger or led,
            DirectorPlan(assessment="a", actions=actions),
            open_keys=set(keys if keys is not None else set()),
            open_count=open_count,
            budget=budget,
            commissions=commissions or [],
            now=now,
        )

    rows: list[tuple[str, list[act.Decision], list[bool], str]] = [
        (
            "a well-formed bounty for a real gap is accepted",
            run([_valid_issue()]),
            [True],
            "",
        ),
        (
            "targetStage the event does not have is rejected",
            run([_valid_issue(targetStage="reception")]),
            [False],
            "not a stage of this event",
        ),
        (
            "a hallucinated personId is rejected — the prompt lists the only legal ones",
            run([_valid_issue(targetVip="Aarav Sharma", targetMoment=None)]),
            [False],
            "not a person the host promoted",
        ),
        (
            "a bounty naming neither a moment nor a person is rejected",
            run([_valid_issue(targetMoment=None)]),
            [False],
            "must name a targetMoment or a targetVip",
        ),
        (
            "a duplicate (stage, moment, vip) is rejected across ticks (spec 05 §1)",
            run([_valid_issue()], keys=open_keys),
            [False],
            "already open",
        ),
        (
            "two identical bounties in ONE plan collapse to one acceptance",
            run([_valid_issue(), _valid_issue()]),
            [True, False],
            "already open",
        ),
        (
            f"the {DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK}-per-tick budget is spent, not exceeded",
            run(
                [
                    _valid_issue(targetMoment="m1"),
                    _valid_issue(targetMoment="m2"),
                    _valid_issue(targetMoment="m3"),
                ]
            ),
            [True, True, False],
            "budget",
        ),
        (
            f"{DIRECTOR_MAX_ACTIVE_BOUNTIES} already open blocks a new one whatever the budget says",
            run([_valid_issue()], open_count=DIRECTOR_MAX_ACTIVE_BOUNTIES),
            [False],
            f"max {DIRECTOR_MAX_ACTIVE_BOUNTIES}",
        ),
        (
            "guest-facing copy that is one word is rejected before it reaches a phone",
            run([_valid_issue(guestFacingCopy="Photo")]),
            [False],
            "guestFacingCopy must be",
        ),
        (
            "a wanted-poster headline that is a paragraph is rejected",
            run([_valid_issue(title="x" * 200)]),
            [False],
            "title must be",
        ),
        (
            "escalating a bountyId that is not open is rejected",
            run([_action(ActionType.ESCALATE_BOUNTY, bountyId="B_NOPE")]),
            [False],
            "not an open bounty",
        ),
        (
            "escalating a real open bounty is accepted",
            run([_action(ActionType.ESCALATE_BOUNTY, bountyId="B_OPEN")]),
            [True],
            "",
        ),
        (
            "the same bounty cannot be escalated twice in one plan",
            run(
                [
                    _action(ActionType.ESCALATE_BOUNTY, bountyId="B_OPEN"),
                    _action(ActionType.ESCALATE_BOUNTY, bountyId="B_OPEN"),
                ]
            ),
            [True, False],
            "already escalated",
        ),
        (
            "advancing to a stage the event does not have is rejected",
            run([_action(ActionType.PROPOSE_STAGE_ADVANCE, toStageId="afterparty", confidence=0.99)]),
            [False],
            "not a stage of this event",
        ),
        (
            "advancing to the stage already active is rejected",
            run([_action(ActionType.PROPOSE_STAGE_ADVANCE, toStageId="sangeet", confidence=0.99)]),
            [False],
            "already the active stage",
        ),
        (
            "a reel persona spec 06 does not define is rejected",
            run([_action(ActionType.COMMISSION_REEL, persona="tiktok_montage")]),
            [False],
            "not one of the spec 06 personas",
        ),
        (
            "a commission already on the director state is rejected",
            run(
                [_action(ActionType.COMMISSION_REEL, persona="couple", stageId="haldi")],
                commissions=[{"persona": "couple", "stageId": "haldi"}],
            ),
            [False],
            "already commissioned",
        ),
        (
            "a 400-character kiosk announcement is rejected",
            run([_action(ActionType.ANNOUNCE, kioskMessage="x" * 400)]),
            [False],
            "must be",
        ),
        (
            "one announcement per tick",
            run(
                [
                    _action(ActionType.ANNOUNCE, kioskMessage="Pheras beginning at the mandap"),
                    _action(ActionType.ANNOUNCE, kioskMessage="Also this"),
                ]
            ),
            [True, False],
            "one announcement per tick",
        ),
        (
            "NO_OP is a valid answer, not an error (spec 05 §5)",
            run([_action(ActionType.NO_OP, reason="coverage is good")]),
            [True],
            "",
        ),
    ]

    for label, decisions, expected, needle in rows:
        got = [d.ok for d in decisions]
        if got != expected:
            fail(f"guardrail: {label} → {got}, expected {expected} ({[d.reason for d in decisions]})")
        if needle:
            reasons = " ".join(d.reason for d in decisions if not d.ok)
            if needle not in reasons:
                fail(f"guardrail: {label} rejected for {reasons!r}, expected a reason mentioning {needle!r}")
        print(f"  ok  {label}")

    # --- the stage-advance guardrail, which is two conditions and not one
    advance_rows = [
        ("confidence 0.95 and the schedule agrees → applies automatically", 0.95, "ceremony", "activeStage", True),
        ("confidence 0.50 → a host suggestion, which is a good outcome", 0.50, "ceremony", "activeStage", False),
        ("confidence 0.99 but the schedule is 6 h away → suggestion only", 0.99, "vidaai", "activeStage", False),
        ("the host is holding the stage manually → suggestion only, at any confidence", 1.0, "ceremony", "override", False),
    ]
    for label, confidence, target, source, auto in advance_rows:
        decisions = run(
            [_action(ActionType.PROPOSE_STAGE_ADVANCE, toStageId=target, confidence=confidence)],
            ledger=_ledger(now, active_source=source),
        )
        if not decisions[0].ok or decisions[0].auto_apply is not auto:
            fail(
                f"guardrail: {label} → ok={decisions[0].ok} auto={decisions[0].auto_apply}, "
                f"expected ok=True auto={auto} ({decisions[0].reason})"
            )
        print(f"  ok  {label}")
    ok("stage advance needs BOTH confidence >= 0.8 and the timetable's agreement (spec 05 §1)")

    # --- points: the money path, as arithmetic
    point_rows = [
        ("a tier-0 Principal saturates the ceiling", 100, 3.0, BOUNTY_POINTS_MAX),
        ("a tier-1 inner-circle gap outpays a generic one", 100, 1.8, 180),
        ("a tier-3 guest gap pays the default", 100, 1.0, 100),
        ("a model asking for 5 points is floored", 5, 1.0, BOUNTY_POINTS_MIN),
        ("a model asking for 10,000 points is capped", 10_000, 3.0, BOUNTY_POINTS_MAX),
        ("a negative weight cannot produce a negative award", 100, -5.0, BOUNTY_POINTS_MIN),
    ]
    for label, base, weight, expected in point_rows:
        got = act.points_for(base, weight)
        if got != expected:
            fail(f"points: {label} → {got}, expected {expected}")
        print(f"  ok  {label}: clamp({base} x {weight}) = {got}")
    ok(f"points always land in [{BOUNTY_POINTS_MIN}, {BOUNTY_POINTS_MAX}] (spec 05 §1, spec 11 §3.3)")

    # --- gap ranking: tier first, severity second (spec 05 §1, spec 11 §3.3 point 3)
    led2 = _ledger(now)
    shards = {
        "sangeet": coverage.StageCoverage("sangeet", photo_count=4, people={}, moments={}),
    }
    gaps = ledger_mod._gaps(led2, shards, now)
    if not gaps:
        fail("gap ranking: an active stage with an uncovered required moment and an unphotographed "
             "Principal produced no gaps")
    if gaps[0].person_id != "P0":
        fail(
            f"gap ranking: the tier-0 Principal is not first (got {gaps[0].kind}/{gaps[0].person_id}) "
            "— spec 05 §1 ranks by vipWeight before severity"
        )
    future = [g for g in gaps if g.stage_id in ("ceremony", "vidaai")]
    if future:
        fail(f"gap ranking: stages that have not started are not gaps yet, got {future}")
    ok(
        f"gaps ranked tier-first ({' > '.join(g.person_id or g.moment_id or '?' for g in gaps[:3])}); "
        "unstarted stages are not gaps"
    )

    # --- session: the rolling window and its arithmetic compaction
    state = session_mod.DirectorState()
    for i in range(DIRECTOR_SESSION_WINDOW + 5):
        state.ticks.append(
            session_mod.TickSummary(
                tickId=f"T{i}", at=now, assessment=f"tick {i}", issued=1, expired=1, stageId="sangeet"
            )
        )
    overflow = state.ticks[:-DIRECTOR_SESSION_WINDOW]
    compacted = session_mod._compact("", overflow)
    if f"{len(overflow)} earlier ticks" not in compacted:
        fail(f"compaction: {compacted!r} does not count the overflowing ticks")
    twice = session_mod._compact(compacted, overflow)
    if f"{len(overflow) * 2} earlier ticks" not in twice:
        fail(
            f"compaction is not additive: {twice!r} — a lossy re-telling of a re-telling is exactly the "
            "context rot the rolling window exists to prevent"
        )
    if len(twice) > 200:
        fail(f"compaction grew to {len(twice)} chars — it must stay one sentence however long the event runs")
    ok(f"compaction is additive arithmetic and stays one sentence: {twice!r}")

    # --- coverage: what the indexing transaction actually writes
    captured: list[tuple[str, dict[str, Any], bool]] = []

    class _FakeTxn:
        def set(self, ref: Any, data: dict[str, Any], merge: bool = False) -> None:
            captured.append((ref.path, data, merge))

    shard_id = coverage.bump(
        _FakeTxn(),
        "e",
        {
            "curator": {
                "stageId": "sangeet",
                "aestheticScore": 0.8,
                "isHighlight": True,
                "momentTags": ["first_dance", "bad.key"],
            },
            "albumOf": ["P0"],
            "visibility": "public",
            "capturedAt": now,
        },
    )
    if shard_id != "sangeet" or not captured:
        fail(f"coverage: bump wrote nothing (shard={shard_id!r})")
    path, data, merge = captured[0]
    if not path.endswith("ledger/coverageShards/stages/sangeet") or not merge:
        fail(f"coverage: wrote {path!r} merge={merge} — expected a merged write to the stage shard")
    for field in ("photoCount", "highlightCount", "publicCount", "aestheticSum", "moments", "people"):
        if field not in data:
            fail(f"coverage: shard update is missing {field}: {sorted(data)}")
    if "bad.key" in data["moments"]:
        fail("coverage: a moment tag containing a dot became a field path — that would corrupt the shard")
    if "first_dance" not in data["moments"] or "P0" not in data["people"]:
        fail(f"coverage: moment/person counters missing: {data['moments']} {data['people']}")
    ok("coverage bump writes one merged, increment-only update to the stage shard, unsafe keys dropped")


# ================================================================ 2. the live run


def seed_event(event_id: str) -> tuple[dict[str, Any], str]:
    """A purpose-built event with a genuinely under-covered active stage and one tier-0 Principal.

    Not `dev_demo`: the demo event's coverage is whatever the last seed left, and a test that only
    passes on a particular history is not a test. This one is reset to a known-empty state every run —
    which is also the only honest way to assert "within one tick a bounty exists".
    """
    now = dt.datetime.now(dt.timezone.utc)
    tz = ZoneInfo("Asia/Kolkata")
    stages = build_stages(now, tz)

    fs.event_ref(event_id).set(
        firestore_ready(
            {
                "eventId": event_id,
                "name": "Story Director Smoke",
                "timezone": "Asia/Kolkata",
                "status": EventStatus.LIVE.value,
                "class": EventClass.INTERNAL_DEV.value,
                "stages": [s.model_dump() for s in stages],
                "activeStage": "sangeet",
                "stageOverride": fs.DELETE_FIELD,
                "eventTypeProfile": {
                    "templateId": "wedding_hindu",
                    "vipTopology": "pyramid",
                    "sensitivityProfile": {},
                    "culturalGlossary": ["haldi", "sangeet", "kanyadaan"],
                },
                "publicFloor": 0.0,  # the fixture portrait must reach `public`, not the aesthetic floor
                "createdAt": now,
                "liveAt": now,
            }
        ),
        merge=True,
    )

    # A host-declared Principal, exactly as the host console's tier endpoint writes one (spec 11 §3.1).
    # No enrollment document: this person has never been photographed, which is the gap under test.
    person_id = new_ulid()
    fs.person_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "displayName": "Aarav (groom)",
            "tier": 0,
            "hostEnrolled": True,
            "uidLinks": [],
            "createdAt": now,
        }
    )
    return fs.get_event(event_id) or {}, person_id


def reset_director(event_id: str) -> None:
    """Clear only what this session owns: bounties, coverage shards, the director state."""
    for snap in fs.bounties_col(event_id).stream():
        snap.reference.delete()
    for snap in fs.people_col(event_id).stream():
        snap.reference.delete()
    coverage.clear(event_id)
    fs.director_state_ref(event_id).delete()
    for snap in fs.ops_col(event_id).stream():
        snap.reference.delete()


def tick(api: str, token: str, event_id: str, *, timeout: float = 180.0) -> dict[str, Any]:
    """Spec 05 §1's host-authed fallback trigger, which is the only way to tick on demand.

    Using it here is not a contradiction of "no human intervention": `smoke_autonomy.py` already proves
    Cloud Scheduler fires this same endpoint unprompted, and a test that waited up to two minutes for
    each of six ticks would take a quarter of an hour. What is under test here is the *decision*, and
    the decision is identical whichever caller authenticated.
    """
    resp = requests.post(
        f"{api}/internal/tick",
        headers={"Authorization": f"Bearer {token}"},
        params={"eventId": event_id},
        timeout=timeout,
    )
    if resp.status_code != 200:
        fail(f"POST /internal/tick failed ({resp.status_code}): {resp.text[:400]}")
    body = resp.json()
    ticked = body.get("ticked") or []
    if len(ticked) != 1:
        fail(f"tick ticked {len(ticked)} events, expected exactly 1: {body}")
    report = ticked[0].get("director") or {}
    if report.get("status") == "failed":
        fail(f"the director failed on this tick: {report.get('error')}")
    return report


def open_bounties(event_id: str) -> list[dict[str, Any]]:
    found = []
    for snap in fs.bounties_col(event_id).stream():
        doc = snap.to_dict() or {}
        doc.setdefault("bountyId", snap.id)
        if str(doc.get("status") or "") in OPEN_STATUSES:
            found.append(doc)
    return found


def check_first_tick(api: str, token: str, event_id: str, person_id: str) -> list[dict[str, Any]]:
    """Spec 05 §5's first criterion, on real documents: within one tick a bounty exists."""
    report = tick(api, token, event_id)
    bounties = open_bounties(event_id)
    if not bounties:
        fail(
            "the first tick issued no bounty. The active stage has two uncovered required moments and "
            f"a tier-0 Principal with no photographs. Report: {report}"
        )

    armed = [b for b in bounties if b.get("source") == "armed"]
    if not armed:
        fail(
            "no bounty was *armed*: a stage becoming active must fire its required-moment bounties from "
            "the timeline immediately (spec 05 §2), not wait for a statistical signal"
        )
    ok(
        f"one tick, nobody asked: {len(bounties)} bounties exist "
        f"({len(armed)} armed from the timeline, {len(bounties) - len(armed)} reasoned) — "
        f"gaps={report.get('gapsFound')} tokens={report.get('tokensIn')}/{report.get('tokensOut')}"
    )
    if not report.get("assessment"):
        fail(f"the tick produced no assessment; the session has nothing to carry forward: {report}")
    print(f"      assessment: {report['assessment'][:200]}")

    for bounty in bounties:
        _check_shape(bounty, person_id)
    listing = ", ".join("{}=+{}".format(str(b.get("title"))[:24], b.get("points")) for b in bounties)
    ok(
        "every bounty is well-formed: clamped points, a dedupe key, guest-facing copy, an expiry "
        f"({listing})"
    )
    return bounties


def _check_shape(bounty: dict[str, Any], person_id: str) -> None:
    bid = bounty.get("bountyId")
    points = int(bounty.get("points") or 0)
    if not BOUNTY_POINTS_MIN <= points <= BOUNTY_POINTS_MAX:
        fail(f"bounty {bid}: points {points} outside the spec 05 §1 band")
    if not bounty.get("dedupeKey"):
        fail(f"bounty {bid}: no dedupeKey — the duplicate guardrail would not survive a restart")
    if not (bounty.get("copy") and bounty.get("title")):
        fail(f"bounty {bid}: missing title/copy, so the banner and the poster have nothing to render")
    if not isinstance(bounty.get("expiresAt"), dt.datetime):
        fail(f"bounty {bid}: no expiresAt — it could never expire into a permanent gap")
    if bounty.get("targetVip") and bounty["targetVip"] != person_id:
        fail(f"bounty {bid}: targetVip {bounty['targetVip']} is not the seeded Principal {person_id}")
    if bounty.get("targetVip") and int(bounty.get("vipWeight") or 0) < 3:
        fail(f"bounty {bid}: a tier-0 target carries vipWeight {bounty.get('vipWeight')}, expected 3.0")


def check_quiet_tick(api: str, token: str, event_id: str, before: list[dict[str, Any]]) -> None:
    """Spec 05 §5's last criterion: nothing changed, so nothing new is asked for. No bounty spam."""
    report = tick(api, token, event_id)
    after = open_bounties(event_id)
    keys = [b.get("dedupeKey") for b in after]
    if len(keys) != len(set(keys)):
        fail(f"duplicate bounty targets after a second tick: {keys}")
    if len(after) > DIRECTOR_MAX_ACTIVE_BOUNTIES:
        fail(f"{len(after)} bounties open, above the spec 05 §1 ceiling of {DIRECTOR_MAX_ACTIVE_BOUNTIES}")
    new = len(after) - len(before)
    ok(
        f"a second tick over the same evidence added {max(0, new)} bounties and no duplicate targets "
        f"(open={len(after)}/{DIRECTOR_MAX_ACTIVE_BOUNTIES}); rejected={report.get('rejected') or 'none'}"
    )
    if report.get("assessment"):
        print(f"      assessment: {report['assessment'][:200]}")


def check_coverage(api_key: str, api: str, event_id: str, timeout: float) -> None:
    """The counters move inside the transaction that indexes the photo, not on a later pass."""
    before = coverage.read(event_id)
    before_total = sum(s.photo_count for s in before.values())

    token, _uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    data = unique_jpeg(PHOTO)
    target = _register(api, event_id, token, media_id, data, ring="public")
    put_bytes(target["signedUrl"], data)

    doc = _wait(
        f"{media_id} to reach status=indexed",
        lambda: (lambda d: d if d.get("status") == "indexed" else None)(
            fs.media_ref(event_id, media_id).get().to_dict() or {}
        ),
        timeout,
    )
    after = coverage.read(event_id)
    after_total = sum(s.photo_count for s in after.values())
    if after_total != before_total + 1:
        fail(
            f"coverage counted {after_total - before_total} photos for one indexed item — the shard is "
            "bumped inside the indexing transaction, so it must move exactly once"
        )
    stage_id = (doc.get("curator") or {}).get("stageId") or coverage.UNSTAGED
    shard = after.get(stage_id)
    if shard is None:
        fail(f"no coverage shard for stage {stage_id!r} after indexing a photo attributed to it")
    ok(
        f"coverage ledger: {stage_id} now photos={shard.photo_count} good={shard.highlight_count} "
        f"public={shard.public_count} meanAesthetic={shard.mean_aesthetic:.2f} "
        f"moments={list(shard.moments)[:3]}"
    )

    # Idempotency: replaying a stage must not double-count, because `status='indexed'` transitions once.
    requests.post(
        f"{api}/v1/events/{event_id}/admin/replay/{media_id}",
        headers={"Authorization": f"Bearer {_HOST_TOKEN}"},
        params={"stage": "curate"},
        timeout=60,
    )
    time.sleep(12)
    replayed = sum(s.photo_count for s in coverage.read(event_id).values())
    if replayed != after_total:
        fail(
            f"a replayed curate stage moved the coverage count {after_total} → {replayed}; the bump is "
            "keyed on the transition into `indexed`, which happens exactly once per item"
        )
    ok("a surgical replay re-runs the stage and does not double-count coverage (spec 03 §6)")


def check_submission(api_key: str, api: str, token: str, event_id: str, bounty: dict[str, Any], timeout: float) -> None:
    """A submission is validated once, awarded once, and never twice — spec 05 §5's second criterion."""
    bounty_id = str(bounty["bountyId"])
    guest_token, uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    data = unique_jpeg(PHOTO)
    target = _register(api, event_id, guest_token, media_id, data, ring="public", bounty_id=bounty_id)
    put_bytes(target["signedUrl"], data)
    _wait(
        f"the submission {media_id} to finish the pipeline",
        lambda: (lambda d: d if d.get("status") == "indexed" else None)(
            fs.media_ref(event_id, media_id).get().to_dict() or {}
        ),
        timeout,
    )

    points_before = int((fs.guest_ref(event_id, uid).get().to_dict() or {}).get("points") or 0)
    report = tick(api, token, event_id)
    validation = report.get("validation") or {}
    media = fs.media_ref(event_id, media_id).get().to_dict() or {}
    if not media.get("bountyCheckedAt"):
        fail(f"the submission was never judged: validation={validation}")
    verdict = media.get("bountyVerdict")
    doc = fs.bounty_ref(event_id, bounty_id).get().to_dict() or {}
    mine = [s for s in (doc.get("submissions") or []) if s.get("mediaId") == media_id]
    if len(mine) != 1:
        fail(f"the bounty records {len(mine)} submissions for one photo: {doc.get('submissions')}")
    points_after = int((fs.guest_ref(event_id, uid).get().to_dict() or {}).get("points") or 0)
    ok(
        f"submission judged once: verdict={verdict} score={mine[0].get('score')} "
        f"points {points_before} → {points_after} ({mine[0].get('reason')})"
    )

    # The double-award guard, on the real documents: the same photo through another tick pays nothing.
    tick(api, token, event_id)
    doc2 = fs.bounty_ref(event_id, bounty_id).get().to_dict() or {}
    again = [s for s in (doc2.get("submissions") or []) if s.get("mediaId") == media_id]
    final = int((fs.guest_ref(event_id, uid).get().to_dict() or {}).get("points") or 0)
    if len(again) != 1 or final != points_after:
        fail(
            f"a second tick re-judged the same submission ({len(again)} entries, points {points_after} "
            f"→ {final}) — spec 05 §5 forbids a double award"
        )
    ok("a second tick does not re-judge or re-pay the same submission (spec 05 §5)")


def check_expiry(api: str, token: str, event_id: str) -> None:
    """An unfulfilled bounty becomes a permanent coverage gap the wrap report has to admit to."""
    remaining = open_bounties(event_id)
    if not remaining:
        print("      SKIP  no open bounty left to expire")
        return
    victim = remaining[0]
    bounty_id = str(victim["bountyId"])
    fs.bounty_ref(event_id, bounty_id).update(
        {"expiresAt": dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)}
    )
    tick(api, token, event_id)

    doc = fs.bounty_ref(event_id, bounty_id).get().to_dict() or {}
    if doc.get("status") != BountyStatus.EXPIRED.value:
        fail(f"a bounty past its expiry is still {doc.get('status')!r}")
    state = fs.director_state_ref(event_id).get().to_dict() or {}
    gaps = [g for g in (state.get("permanentGaps") or []) if g.get("bountyId") == bounty_id]
    if not gaps:
        fail(
            "the expired bounty was not recorded as a permanent coverage gap; the wrap report would "
            "silently omit something the system asked for and never got (spec 05 §3)"
        )
    ok(f"expired → recorded as a permanent gap: {gaps[0].get('title')!r} ({gaps[0].get('dedupeKey')})")


def check_session(event_id: str) -> None:
    """The rolling window, on the real document."""
    state = fs.director_state_ref(event_id).get().to_dict() or {}
    ticks = state.get("ticks") or []
    if not ticks:
        fail("the director state carries no tick history — every tick reasons from a cold start")
    if len(ticks) > DIRECTOR_SESSION_WINDOW:
        fail(f"{len(ticks)} ticks in the window, above the spec 05 §1 bound of {DIRECTOR_SESSION_WINDOW}")
    if not state.get("lastStageId"):
        fail("no lastStageId recorded — the next tick would re-arm this stage's bounties")
    ok(
        f"session: {state.get('tickCount')} ticks recorded, window holds {len(ticks)} "
        f"(bound {DIRECTOR_SESSION_WINDOW}), lastStage={state.get('lastStageId')}"
    )
    for entry in ticks[-3:]:
        print(f"      {entry.get('at')} {','.join(entry.get('actions') or ['NO_OP'])}")


# ---------------------------------------------------------------- small helpers

_HOST_TOKEN = ""


def _register(
    api: str,
    event_id: str,
    token: str,
    media_id: str,
    data: bytes,
    *,
    ring: str,
    bounty_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "batchId": new_ulid(),
        "consent": {"public": ring == "public", "selfOnly": ring == "self"},
        "files": [
            {
                "clientMediaId": media_id,
                "fileName": f"{media_id}.jpg",
                "contentType": "image/jpeg",
                "size": len(data),
            }
        ],
    }
    if bounty_id:
        body["bountyId"] = bounty_id
    resp = requests.post(
        f"{api}/v1/events/{event_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=60,
    )
    if resp.status_code != 200:
        fail(f"POST /uploads failed ({resp.status_code}): {resp.text[:400]}")
    payload = resp.json()
    if bounty_id and payload.get("bountyId") != bounty_id:
        fail(f"the API dropped the bountyId {bounty_id} (returned {payload.get('bountyId')!r})")
    return payload["uploads"][0]


def _wait(label: str, predicate: Any, timeout: float, poll: float = 2.0) -> Any:
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        try:
            value = predicate()
        except Exception as exc:  # noqa: BLE001 - a flaky dev uplink is not what is under test
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(poll)
            continue
        if value:
            return value
        time.sleep(poll)
    fail(f"timed out after {timeout:.0f}s waiting for {label}{f' (last: {last})' if last else ''}")


# ================================================================ main


def main() -> int:
    global _HOST_TOKEN
    cfg = settings()
    ap = argparse.ArgumentParser(description="Smoke-test the Story Director (S8b).")
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--event-id", default=EVENT_ID)
    ap.add_argument("--guardrails-only", action="store_true", help="the offline table only")
    ap.add_argument("--keep", action="store_true", help="leave the test event live afterwards")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    print("── the guardrails (spec 05 §1/§5) — pure, offline, no spend")
    check_guardrails()
    if args.guardrails_only:
        print("\nPASS  guardrail decision table only (--guardrails-only)")
        return 0

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")
    if not PHOTO.exists():
        fail(f"fixture missing: {PHOTO} (run scripts/risk_tests/banana.py to regenerate the cast)")

    event_id = args.event_id
    print(f"\n── the event under test: {event_id} on {cfg.project}")
    reset_director(event_id)
    event, person_id = seed_event(event_id)
    ok(
        f"seeded: activeStage={event.get('activeStage')} with 2 uncovered required moments, "
        f"1 tier-0 Principal ({person_id}) with no photographs, 0 bounties, empty coverage ledger"
    )
    _HOST_TOKEN = mint_host_token(event_id, api_key)

    try:
        print("\n── one tick: does it notice, and does it act? (spec 05 §5.1)")
        bounties = check_first_tick(api, _HOST_TOKEN, event_id, person_id)

        print("\n── the coverage ledger moves with the pipeline, exactly once (§4.18)")
        check_coverage(api_key, api, event_id, args.timeout)

        print("\n── a bounty submission is judged once and paid once (spec 05 §5.2)")
        check_submission(api_key, api, _HOST_TOKEN, event_id, bounties[0], args.timeout)

        print("\n── nothing changed, so nothing new is asked for (spec 05 §5.5)")
        check_quiet_tick(api, _HOST_TOKEN, event_id, open_bounties(event_id))

        print("\n── what it asked for and never got (spec 05 §3)")
        check_expiry(api, _HOST_TOKEN, event_id)

        print("\n── tick-to-tick memory, bounded (spec 05 §1, HANDOFF §4.18)")
        check_session(event_id)
    finally:
        if not args.keep:
            # A live event costs a `gemini-3.7-flash` call on every production tick, forever
            # (HANDOFF §9's judge-month exposure note). A test does not get to leave one behind.
            fs.event_ref(event_id).update(
                {"status": EventStatus.WRAPPED.value, "wrappedAt": fs.SERVER_TIMESTAMP}
            )
            print(f"\n      cleanup: {event_id} set to wrapped (pass --keep to leave it live)")

    print()
    print(f"PASS  {event_id}: the fleet noticed a coverage gap and asked a crowd to fix it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
