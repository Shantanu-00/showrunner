"""Smoke-test the multi-day timeline machinery (spec 13) — day math, gap lifecycle, idle economy.

Companion to `smoke_director.py`, which proves the director decides; this proves the director can
tell *what day it is*: that a five-day trip's Day 1 misses stop bidding against Day 4's live gaps,
that photo evidence can move the timeline when the schedule is loose, and that the 3 a.m. ticks of
a multi-day event stop paying for model calls.

Two halves, the same split every smoke script in this repo uses:

  1. offline (default) — pure truth tables over `shared/eventtime.py`, `shared/stages.py`,
     `ledger._gaps`, `act.archive_lapsed_stages`, `act._decide_advance`, `director._is_idle`'s
     cheap paths and `publisher.store.event_context`. No network, no Firestore, no spend.
  2. `--api https://api-...run.app` — seeds a real 2-day `internal_dev` event whose Day-1 stage
     lapsed uncovered and whose Day-2 stage is running now, ticks it through the real
     `POST /internal/tick`, and asserts against real documents: nothing armed or issued for the
     lapsed moment, the current stage armed, the lapse archived once into
     `directorState.permanentGaps`; then a second event with nothing scheduled for days reports
     `mode: "idle"` and leaves no session line.

    python scripts/smoke_multiday.py                        # offline, free
    python scripts/smoke_multiday.py --api https://api-...run.app
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from directors.story import act, director as director_mod, ledger as ledger_mod, session as session_mod  # noqa: E402
from publisher import store as pub_store  # noqa: E402
from schemas.director import ActionType, DirectorAction  # noqa: E402
from schemas.event import EventClass, EventStatus  # noqa: E402
from shared.eventtime import EventCalendar  # noqa: E402
from shared.settings import (  # noqa: E402
    DRIFT_ADVANCE_TICKS,
    STAGE_ADVANCE_WINDOW_MINUTES,
    STAGE_GAP_GRACE_MINUTES,
    TICK_IDLE_LOOKAHEAD_MINUTES,
)
from shared.stages import resolve_active, scheduled_stage_id  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UTC = dt.timezone.utc


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ================================================================ offline truth tables


def _stage_view(
    stage_id: str,
    starts: dt.datetime | None,
    ends: dt.datetime | None,
    moments: tuple[str, ...] = (),
    counts: dict[str, int] | None = None,
) -> ledger_mod.StageView:
    return ledger_mod.StageView(
        stage_id=stage_id,
        label=stage_id,
        starts_at=starts,
        ends_at=ends,
        required_moments=tuple((m, m, 1.0) for m in moments),
        photo_count=sum((counts or {}).values()),
        public_count=0,
        highlight_count=0,
        mean_aesthetic=0.0,
        last_captured_at=None,
        moment_counts=counts or {},
    )


def _trip_calendar() -> EventCalendar:
    return EventCalendar.of(
        {"timezone": "Asia/Tokyo", "startsOn": "2026-10-12", "endsOn": "2026-10-16"}
    )


def check_eventtime() -> None:
    cal = _trip_calendar()
    at = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)  # 19:00 JST, trip day 3
    rows = [
        ("day index counts in the event's wall clock", cal.day_index(at), 3),
        ("day count spans the range inclusive", cal.day_count(), 5),
        ("the prompt stamp names the day", cal.stamp(at), "Day 3 Wed 19:00"),
        (
            "a same-day window prints one day marker",
            cal.window_text(
                dt.datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
                dt.datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
            ),
            "[Day 3 Wed 18:00-21:00]",
        ),
        (
            "a cross-midnight window prints both ends in full",
            cal.window_text(
                dt.datetime(2026, 10, 14, 13, 0, tzinfo=UTC),
                dt.datetime(2026, 10, 14, 16, 0, tzinfo=UTC),
            ),
            "[Day 3 Wed 22:00 - Day 4 Thu 01:00]",
        ),
    ]
    undated = EventCalendar.of({"timezone": "Asia/Tokyo"})
    rows += [
        ("an undated event has no day index", undated.day_index(at), None),
        ("an undated stamp is time-only — the pre-spec-13 form", undated.stamp(at), "Wed 19:00"),
        (
            "an undated window is time-only — the pre-spec-13 form",
            undated.window_text(
                dt.datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
                dt.datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
            ),
            "[18:00-21:00]",
        ),
        ("a malformed date degrades to undated, never raises",
         EventCalendar.of({"timezone": "Asia/Tokyo", "startsOn": "not-a-date"}).day_index(at), None),
        ("an unknown timezone degrades to UTC, never raises",
         EventCalendar.of({"timezone": "Mars/Olympus", "startsOn": "2026-10-12"}).day_index(at), 3),
    ]
    for label, got, want in rows:
        if got != want:
            fail(f"eventtime: {label}: got {got!r}, wanted {want!r}")
        print(f"  ok  {label}")
    ok("day math derives from startsOn+timezone and degrades to time-only when undated")


def check_resolver() -> None:
    now = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    scheduled = {
        "stages": [
            {
                "stageId": "gion",
                "startsAt": dt.datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
                "endsAt": dt.datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
            }
        ]
    }
    rows = [
        ("override beats everything", {"stageOverride": "x", "activeStage": "y", **scheduled}, ("x", "override")),
        ("activeStage beats the schedule", {"activeStage": "y", **scheduled}, ("y", "activeStage")),
        ("the schedule answers when nothing was written", scheduled, ("gion", "schedule")),
        ("no window contains now → none", {"stages": scheduled["stages"]}, None),
    ]
    later = dt.datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
    for label, event, want in rows:
        got = resolve_active(event, now if want is not None else later)
        want = want or (None, "none")
        if got != want:
            fail(f"resolver: {label}: got {got!r}, wanted {want!r}")
        print(f"  ok  {label}")
    if scheduled_stage_id({"stages": [{"stageId": "s", "startsAt": None, "endsAt": None}]}, now) is not None:
        fail("resolver: an unscheduled stage must never be the schedule's answer")
    ok("one resolver: override || activeStage || schedule — every surface reads the same function")


def _five_day_ledger(now: dt.datetime) -> ledger_mod.Ledger:
    """Day-2 dinner lapsed uncovered, this morning's lunch lapsed uncovered, viewpoint running now."""
    stages = [
        _stage_view(
            "day2_dinner",
            dt.datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
            dt.datetime(2026, 10, 13, 12, 0, tzinfo=UTC),
            moments=("group_shot",),
        ),
        _stage_view(
            "fuji_lunch",
            dt.datetime(2026, 10, 14, 3, 0, tzinfo=UTC),
            dt.datetime(2026, 10, 14, 5, 0, tzinfo=UTC),
            moments=("table_moment",),
        ),
        _stage_view(
            "fuji_viewpoint",
            dt.datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
            dt.datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
            moments=("establishing_shot",),
        ),
    ]
    return ledger_mod.Ledger(
        event_id="e",
        event_name="Japan 2026",
        now=now,
        status="live",
        active_stage_id="fuji_viewpoint",
        active_stage_label="fuji_viewpoint",
        active_source="schedule",
        scheduled_stage_id="fuji_viewpoint",
        calendar=_trip_calendar(),
        stages=stages,
    )


def check_gap_lifecycle() -> None:
    now = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    led = _five_day_ledger(now)
    gaps = ledger_mod._gaps(led, {}, now)  # noqa: SLF001 - the table exists to pin this function
    gap_stages = {g.stage_id for g in gaps}
    if gap_stages != {"fuji_viewpoint"}:
        fail(f"gap lifecycle: lapsed stages leaked into the live gaps: {gap_stages}")
    print("  ok  a stage past endsAt+grace emits no gaps — Day 1 cannot crowd Day 4 out")

    inside_grace = dt.datetime(2026, 10, 14, 6, 0, tzinfo=UTC)  # lunch ended 05:00, grace 90 min
    led2 = _five_day_ledger(inside_grace)
    led2 = ledger_mod.Ledger(**{**led2.__dict__, "now": inside_grace, "active_stage_id": "fuji_lunch",
                                "active_stage_label": "fuji_lunch", "scheduled_stage_id": None})
    gaps2 = ledger_mod._gaps(led2, {}, inside_grace)  # noqa: SLF001
    if "fuji_lunch" not in {g.stage_id for g in gaps2}:
        fail("gap lifecycle: a stage inside its grace window must still be a live gap")
    print(f"  ok  inside the {STAGE_GAP_GRACE_MINUTES}-min grace window the gap is still live")

    prompt = led.as_prompt_block()
    if "Day 3" not in prompt or "(day 3 of 5)" not in prompt:
        fail("prompt: the header does not name the day")
    if "ENDED" not in prompt:
        fail("prompt: a lapsed stage is not marked ENDED")
    print("  ok  the prompt names the day and marks lapsed stages ENDED")

    state = session_mod.DirectorState()
    archived, records = act.archive_lapsed_stages(led, state)
    if set(archived) != {"day2_dinner", "fuji_lunch"}:
        fail(f"archive: wrong stages archived: {archived}")
    if {r["targetMoment"] for r in records} != {"group_shot", "table_moment"}:
        fail(f"archive: wrong moments recorded: {records}")
    state.archived_stage_ids = archived
    again = act.archive_lapsed_stages(led, state)
    if again != ([], []):
        fail(f"archive: not exactly-once: {again}")
    ok("lapsed stages archive their uncovered moments into permanentGaps exactly once")


def check_advance() -> None:
    now = dt.datetime(2026, 10, 14, 14, 0, tzinfo=UTC)  # 5 h past the viewpoint's start
    led = _five_day_ledger(now)
    led.active_stage_id = "fuji_lunch"
    led.active_source = "activeStage"
    stage_ids = {s.stage_id for s in led.stages}
    action = DirectorAction(
        type=ActionType.PROPOSE_STAGE_ADVANCE, toStageId="fuji_viewpoint", confidence=0.9
    )
    target = next(s for s in led.stages if s.stage_id == "fuji_viewpoint")

    window = act._advance_window_minutes(led, target)  # noqa: SLF001
    if window != max(float(STAGE_ADVANCE_WINDOW_MINUTES), 0.25 * 6 * 60):
        fail(f"advance: window formula wrong: {window}")
    print(f"  ok  a 6-hour-grained schedule widens the window to {window:.0f} min (never below "
          f"{STAGE_ADVANCE_WINDOW_MINUTES})")

    rows = [
        ("no drift, outside the window → suggestion only",
         ledger_mod.Drift(0, 0, None, False), 0, False),
        (f"drift streak {DRIFT_ADVANCE_TICKS} at the same target → the photos move the timeline",
         ledger_mod.Drift(20, 14, "fuji_viewpoint", True), DRIFT_ADVANCE_TICKS, True),
        ("one tick of drift is a burst, not a place → suggestion only",
         ledger_mod.Drift(20, 14, "fuji_viewpoint", True), 1, False),
        ("a streak pointing at a different stage licenses nothing",
         ledger_mod.Drift(20, 14, "day2_dinner", True), 5, False),
    ]
    for label, drift, streak, auto in rows:
        led.drift, led.drift_streak = drift, streak
        decision = act._decide_advance(action, led, stage_ids, now)  # noqa: SLF001
        if not decision.ok or decision.auto_apply is not auto:
            fail(f"advance: {label}: auto={decision.auto_apply} ({decision.reason})")
        print(f"  ok  {label}")

    led.drift, led.drift_streak = ledger_mod.Drift(20, 14, "fuji_viewpoint", True), 5
    led.active_source = "override"
    decision = act._decide_advance(action, led, stage_ids, now)  # noqa: SLF001
    if decision.auto_apply or "manually" not in decision.reason:
        fail("advance: the host's override must beat any amount of evidence")
    ok("evidence-driven advance: sustained drift moves the stage; the override always wins")


def check_idle_cheap_paths() -> None:
    """Only the branches that need no Firestore; the truthy path is the live half's job."""
    now = dt.datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
    far = dt.datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    dated = {"startsAt": far, "endsAt": far + dt.timedelta(hours=2), "stageId": "s"}
    rows = [
        ("a host holding a stage keeps the director awake",
         {"stageOverride": "s", "stages": [dated]}),
        ("an event with no stages keeps its pre-spec-13 behavior", {"stages": []}),
        ("an unscheduled stage keeps the director awake",
         {"stages": [{"stageId": "s", "startsAt": None, "endsAt": None}]}),
        (f"a stage within the {TICK_IDLE_LOOKAHEAD_MINUTES}-min lookahead keeps it awake",
         {"stages": [{"stageId": "s", "startsAt": now + dt.timedelta(minutes=60),
                      "endsAt": now + dt.timedelta(minutes=120)}]}),
        ("a stage still inside the grace window behind keeps it awake",
         {"stages": [{"stageId": "s", "startsAt": now - dt.timedelta(hours=3),
                      "endsAt": now - dt.timedelta(minutes=30)}]}),
    ]
    for label, event in rows:
        if director_mod._is_idle("e", event, now):  # noqa: SLF001
            fail(f"idle: {label}: reported idle")
        print(f"  ok  {label}")
    ok("the idle predicate's cheap paths all fail safe (awake); the true path is asserted live")


def check_previous_by_time() -> None:
    """`event_context` takes the event dict, so this needs no Firestore."""
    mk = lambda sid, day, hour: {  # noqa: E731
        "stageId": sid,
        "startsAt": dt.datetime(2026, 10, day, hour, 0, tzinfo=UTC),
        "endsAt": dt.datetime(2026, 10, day, hour + 2, 0, tzinfo=UTC),
    }
    # Deliberately shuffled: previous must come from time, not array position.
    event = {
        "activeStage": "viewpoint",
        "stages": [mk("viewpoint", 14, 9), mk("arrival", 12, 4), mk("lunch", 14, 3)],
    }
    ctx = pub_store.event_context("e", event)
    if ctx is None or ctx.previous_stage_id != "lunch":
        fail(f"previous-by-time: got {ctx.previous_stage_id if ctx else None}, wanted 'lunch'")
    print("  ok  previous = latest-starting stage before the active one, whatever the array order")

    undated = {
        "activeStage": "b",
        "stages": [{"stageId": "a"}, {"stageId": "b"}, {"stageId": "c"}],
    }
    ctx2 = pub_store.event_context("e", undated)
    if ctx2 is None or ctx2.previous_stage_id != "a":
        fail(f"previous-by-time: undated fallback got {ctx2.previous_stage_id if ctx2 else None}")
    print("  ok  undated stages fall back to array order — the only order they have")

    # And the schedule leg reaches the wall: no activeStage written, a window containing now.
    live_now = dt.datetime.now(UTC)
    scheduled = {
        "stages": [
            {
                "stageId": "running",
                "startsAt": live_now - dt.timedelta(hours=1),
                "endsAt": live_now + dt.timedelta(hours=1),
                "theme": "ocean",
            }
        ]
    }
    ctx3 = pub_store.event_context("e", scheduled)
    if ctx3 is None or ctx3.active_stage_id != "running" or ctx3.theme != "ocean":
        fail("previous-by-time: the publisher does not read the schedule leg")
    ok("the kiosk themes off the schedule before any tick or host action has written a stage")


def check_session_day_lines() -> None:
    cal = _trip_calendar()
    at = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    summary = session_mod.TickSummary(tickId="t", at=at, assessment="x", day=cal.stamp(at))
    line = summary.as_line()
    if not line.startswith("[Day 3 Wed 19:00"):
        fail(f"session: day missing from the tick line: {line!r}")
    round_trip = session_mod.TickSummary.from_doc(summary.to_doc())
    if round_trip.day != summary.day:
        fail("session: day does not survive the document round trip")
    legacy = session_mod.TickSummary.from_doc({"tickId": "old", "at": at})
    if not legacy.as_line().startswith("[10:00"):
        fail(f"session: a pre-spec-13 tick line regressed: {legacy.as_line()!r}")
    ok("the director's own history names the day; pre-spec-13 lines render as before")


# ================================================================ the live half


def _mint_stage(stage_id: str, label: str, starts: dt.datetime, ends: dt.datetime, moment: str) -> dict:
    return {
        "stageId": stage_id,
        "label": label,
        "startsAt": starts,
        "endsAt": ends,
        "requiredMoments": [{"momentId": moment, "label": moment.replace("_", " "), "tierWeight": 1.0}],
        "theme": None,
        "expectedSetting": None,
    }


def live_half(api: str) -> None:
    import os  # noqa: PLC0415

    from smoke_faces import mint_host_token  # noqa: PLC0415 - needs GOOGLE_* env, offline half doesn't
    from shared import fs  # noqa: PLC0415
    from shared.settings import settings  # noqa: PLC0415
    from shared.ulid import new_ulid  # noqa: PLC0415

    cfg = settings()
    cfg.require("project")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")
    now = dt.datetime.now(UTC)

    # --- event A: Day-1 stage lapsed uncovered, Day-2 stage running now
    event_id = f"dev_multiday_{new_ulid().lower()[:8]}"
    yesterday = now - dt.timedelta(days=1)
    fs.event_ref(event_id).set(
        {
            "eventId": event_id,
            "name": "Multiday Smoke Trip",
            "timezone": "Asia/Tokyo",
            "status": EventStatus.LIVE.value,
            "class": EventClass.INTERNAL_DEV.value,
            "startsOn": yesterday.date().isoformat(),
            "endsOn": (now + dt.timedelta(days=1)).date().isoformat(),
            "expectedParticipants": 4,
            "stages": [
                _mint_stage(
                    "day1_dinner", "Day 1 dinner",
                    yesterday - dt.timedelta(hours=2), yesterday, "table_moment",
                ),
                _mint_stage(
                    "day2_walk", "Day 2 walk",
                    now - dt.timedelta(minutes=30), now + dt.timedelta(hours=2), "street_moment",
                ),
            ],
            "eventTypeProfile": {"vipTopology": "pyramid",
                                 "sensitivityProfile": {"pda": "context_dependent",
                                                        "alcohol": "context_dependent",
                                                        "attire": "standard"},
                                 "culturalGlossary": [], "requiredMomentsTemplate": []},
            "createdAt": now,
            "liveAt": now,
        }
    )

    # Seed the lapsed stage's coverage shard directly (the exact field names `coverage.read`
    # expects): the diary distills *counts*, and counts written here are indistinguishable from
    # counts bumped by the indexing transaction — without making this smoke depend on the
    # Curator's visual attribution of a fixture portrait to a stage named "dinner".
    fs.coverage_stage_shard_ref(event_id, "day1_dinner").set(
        {
            "stageId": "day1_dinner",
            "photoCount": 2,
            "publicCount": 1,
            "highlightCount": 1,
            "aestheticSum": 1.5,
            "moments": {},
            "scenes": {"indoor_venue": 2},
            "peopleBuckets": {"p2_3": 1},
            "updatedAt": now,
        }
    )

    token = mint_host_token(event_id, api_key)
    resp = requests.post(
        f"{api}/internal/tick",
        params={"eventId": event_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    if resp.status_code != 200:
        fail(f"tick failed ({resp.status_code}): {resp.text[:300]}")
    ticked = (resp.json().get("ticked") or [{}])[0]
    report = ticked.get("director") or {}
    if report.get("mode") == "idle":
        fail("event A ticked idle — a running stage must keep the director awake")

    bounties = [snap.to_dict() or {} for snap in fs.bounties_col(event_id).stream()]
    lapsed_targets = [b for b in bounties if b.get("targetStage") == "day1_dinner"]
    armed_current = [b for b in bounties if b.get("targetStage") == "day2_walk" and b.get("source") == "armed"]
    if lapsed_targets:
        fail(f"a bounty was issued for the lapsed Day-1 stage: {lapsed_targets}")
    if not armed_current:
        fail(f"the running stage's required moment was not armed: {bounties}")
    print("  ok  live: nothing asked for yesterday's lapsed moment; today's armed on transition")

    state = fs.director_state_ref(event_id).get().to_dict() or {}
    if "day1_dinner" not in (state.get("archivedStages") or []):
        fail(f"the lapsed stage was not archived: {state.get('archivedStages')}")
    lapsed_records = [
        g for g in (state.get("permanentGaps") or []) if g.get("targetStage") == "day1_dinner"
    ]
    if not lapsed_records:
        fail("the lapsed stage's uncovered moment did not reach permanentGaps")
    print("  ok  live: the lapse is archived once into directorState.permanentGaps")

    diary_doc = fs.ledger_ref(event_id, "diary_day1_dinner").get().to_dict() or {}
    if not (diary_doc.get("memo") or "").strip():
        fail(f"the lapsed chapter got no diary memo: {diary_doc}")
    print(f"  ok  live: the Event Diary wrote the chapter's memo ({len(diary_doc['memo'])} chars)")

    # --- the wrap: the first `wrapping` tick commissions the recap film, deterministically,
    # and finalize produces the upgraded report — day labels, gap details, the recap's id.
    fs.event_ref(event_id).update({"status": "wrapping"})
    resp = requests.post(
        f"{api}/internal/tick",
        params={"eventId": event_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    if resp.status_code != 200:
        fail(f"wrapping tick failed ({resp.status_code}): {resp.text[:200]}")
    state = fs.director_state_ref(event_id).get().to_dict() or {}
    recap_entries = [
        c for c in (state.get("commissions") or []) if c.get("persona") == "event_recap"
    ]
    if not recap_entries:
        fail("the wrapping tick did not commission an event_recap")
    print(
        f"  ok  live: the wrapping tick commissioned the recap autonomously "
        f"(status={recap_entries[0].get('status')})"
    )

    resp = requests.post(
        f"{api}/v1/events/{event_id}/lifecycle/finalize",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    if resp.status_code != 200:
        fail(f"finalize failed ({resp.status_code}): {resp.text[:300]}")
    report = resp.json()
    rows = {r["stageId"]: r for r in report.get("perStage") or []}
    # The expected label comes from the same calendar the backend derives from — the smoke's own
    # fixture anchors `startsOn` to the UTC date while the event runs in Asia/Tokyo, so a
    # hardcoded "Day 1" here would be asserting the off-by-one the calendar exists to prevent.
    expected_label = EventCalendar.of(fs.get_event(event_id) or {}).day_label(
        yesterday - dt.timedelta(hours=1)
    )
    got_label = rows.get("day1_dinner", {}).get("dayLabel")
    if not expected_label or got_label != expected_label:
        fail(f"wrap report day label {got_label!r}, calendar says {expected_label!r}")
    detailed = [
        g for g in (report.get("honestGaps") or [])
        if g.get("targetStage", g.get("stageId")) == "day1_dinner" and g.get("detail")
    ]
    if not detailed:
        fail(f"the honest gap carries no detail: {report.get('honestGaps')}")
    if "recapReelId" not in report:
        fail("the wrap report has no recapReelId field")
    if recap_entries[0].get("status") == "producing" and report.get("recapReelId") != recap_entries[0].get("reelId"):
        fail(f"recapReelId does not match the commission: {report.get('recapReelId')}")
    print("  ok  live: the wrap report carries day labels, gap details and the recap's id")

    # --- event B: nothing scheduled for days → the tick reports idle and records no session line
    idle_id = f"dev_idle_{new_ulid().lower()[:8]}"
    fs.event_ref(idle_id).set(
        {
            "eventId": idle_id,
            "name": "Idle Smoke Trip",
            "timezone": "Asia/Tokyo",
            "status": EventStatus.LIVE.value,
            "class": EventClass.INTERNAL_DEV.value,
            "startsOn": now.date().isoformat(),
            "endsOn": (now + dt.timedelta(days=4)).date().isoformat(),
            "stages": [
                _mint_stage(
                    "far_dinner", "Far dinner",
                    now + dt.timedelta(days=2), now + dt.timedelta(days=2, hours=2), "table_moment",
                )
            ],
            "eventTypeProfile": {"vipTopology": "pyramid",
                                 "sensitivityProfile": {"pda": "context_dependent",
                                                        "alcohol": "context_dependent",
                                                        "attire": "standard"},
                                 "culturalGlossary": [], "requiredMomentsTemplate": []},
            "createdAt": now,
            "liveAt": now,
        }
    )
    idle_token = mint_host_token(idle_id, api_key)
    resp = requests.post(
        f"{api}/internal/tick",
        params={"eventId": idle_id},
        headers={"Authorization": f"Bearer {idle_token}"},
        timeout=120,
    )
    if resp.status_code != 200:
        fail(f"idle tick failed ({resp.status_code}): {resp.text[:300]}")
    idle_report = ((resp.json().get("ticked") or [{}])[0]).get("director") or {}
    if idle_report.get("mode") != "idle":
        fail(f"event B should have ticked idle: {idle_report}")
    if int(idle_report.get("tokensIn") or 0) or int(idle_report.get("tokensOut") or 0):
        fail(f"an idle tick spent tokens: {idle_report}")
    idle_state = fs.director_state_ref(idle_id).get().to_dict() or {}
    if idle_state.get("ticks"):
        fail("an idle tick left a session line — it should leave the window untouched")
    print("  ok  live: a quiet event ticks idle — zero tokens, no session churn")

    for eid in (event_id, idle_id):
        fs.event_ref(eid).update({"status": EventStatus.WRAPPED.value})
    ok(f"live half green ({event_id}, {idle_id} wrapped, not deleted — internal_dev housekeeping)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=None, help="api base URL; omitted = offline half only")
    args = parser.parse_args()

    check_eventtime()
    check_resolver()
    check_gap_lifecycle()
    check_advance()
    check_idle_cheap_paths()
    check_previous_by_time()
    check_session_day_lines()

    if args.api:
        live_half(args.api.rstrip("/"))
        print("\nPASS  multi-day machinery, offline + live")
    else:
        print("\nPASS  multi-day truth tables (offline only — pass --api for the live half)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
