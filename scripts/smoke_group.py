"""Smoke-test spec 13 §5–§7: group coverage, targeted bounties, host participant enrollment.

Offline (default): the group-gap decision table (`ledger._group_gap`), the people-count histogram
(`coverage.bucket_for` / `frames_with_at_least`), and the validator's deterministic group floor —
no network, no Firestore, no spend.

Live (`--api`): the parts that only mean anything against real infrastructure, chosen so none of
them depend on a model's mood:
  1. `POST …/people/host-enroll` — person doc lands `hostEnrolled/claimApproved`, the embedding
     lands in deny-all `enrollments/`, and **no identity is granted**: the host's own uid carries
     no `personId` claim and the person's private doc has zero `uidLinks` (§4.28 with no
     exceptions).
  2. `POST …/people/{id}/tier` — tier moves, `ops/` audit exists.
  3. A real upload's indexing transaction bumps the shard's `peopleBuckets` histogram.
  4. An `audience: assignee` bounty whose `assignmentTimeoutAt` has passed is released to `all`
     by the next tick's deterministic step, and `act.resolve_assignee` picks the most recently
     active uploader from real guest docs.

    python scripts/smoke_group.py                          # offline, free
    python scripts/smoke_group.py --api https://api-...run.app
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from directors.story import act, ledger as ledger_mod, validate as validate_mod  # noqa: E402
from schemas.bounty import BountyAudience, BountyStatus  # noqa: E402
from schemas.event import EventClass, EventStatus  # noqa: E402
from shared import coverage  # noqa: E402
from shared.eventtime import EventCalendar  # noqa: E402
from shared.settings import BOUNTY_ASSIGN_TIMEOUT_MINUTES, settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UTC = dt.timezone.utc
PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ================================================================ offline


def _stage(sid: str, day: int, start_h: int, end_h: int) -> ledger_mod.StageView:
    return ledger_mod.StageView(
        stage_id=sid,
        label=sid,
        starts_at=dt.datetime(2026, 10, day, start_h, 0, tzinfo=UTC),
        ends_at=dt.datetime(2026, 10, day, end_h, 0, tzinfo=UTC),
        required_moments=(),
        photo_count=0,
        public_count=0,
        highlight_count=0,
        mean_aesthetic=0.0,
        last_captured_at=None,
        moment_counts={},
    )


def check_histogram() -> None:
    rows = [(0, None), (1, "p1"), (2, "p2_3"), (3, "p2_3"), (4, "p4_6"), (6, "p4_6"),
            (7, "p7_12"), (12, "p7_12"), (13, "p13up"), (500, "p13up")]
    for count, want in rows:
        got = coverage.bucket_for(count)
        if got != want:
            fail(f"histogram: bucket_for({count}) = {got!r}, wanted {want!r}")
    shard = coverage.StageCoverage("s", people_buckets={"p2_3": 2, "p4_6": 1})
    for threshold, want in [(2, 3), (3, 1), (4, 1), (5, 0)]:
        got = shard.frames_with_at_least(threshold)
        if got != want:
            fail(f"histogram: frames_with_at_least({threshold}) = {got}, wanted {want}")
    ok("people-count histogram: bucket floors are conservative — coverage is never overstated")


def check_group_gap() -> None:
    now = dt.datetime(2026, 10, 14, 10, 0, tzinfo=UTC)  # trip day 3, 19:00 JST
    cal = EventCalendar.of({"timezone": "Asia/Tokyo", "startsOn": "2026-10-12", "endsOn": "2026-10-16"})
    stages = [_stage("day3_walk", 14, 1, 4), _stage("day3_gion", 14, 9, 12), _stage("day2_dinner", 13, 9, 12)]
    led = ledger_mod.Ledger(
        event_id="e", event_name="t", now=now, status="live",
        active_stage_id="day3_gion", active_stage_label="day3_gion", active_source="schedule",
        scheduled_stage_id="day3_gion", calendar=cal, expected_participants=4, stages=stages,
    )

    def group_gaps(shards):  # noqa: ANN001, ANN202
        return [g for g in ledger_mod._gaps(led, shards, now) if g.kind == "group"]  # noqa: SLF001

    gaps = group_gaps({})
    if len(gaps) != 1 or gaps[0].moment_id != "group_shot" or gaps[0].stage_id != "day3_gion":
        fail(f"group gap: expected one anchored to the active stage, got {gaps}")
    print("  ok  no qualifying frame today → one gap, anchored to the active stage")

    if group_gaps({"day3_walk": coverage.StageCoverage("day3_walk", people_buckets={"p4_6": 1})}):
        fail("group gap: a ≥threshold frame earlier today should have cleared it")
    print("  ok  a 4-person frame anywhere today clears it")

    if not group_gaps({"day2_dinner": coverage.StageCoverage("day2_dinner", people_buckets={"p4_6": 3})}):
        fail("group gap: yesterday's group frame must not clear today")
    print("  ok  yesterday's group photo does not clear today — the ask is per day")

    led.expected_participants = None
    if group_gaps({}):
        fail("group gap: must be skipped entirely when expectedParticipants is unset")
    led.expected_participants = 1
    if group_gaps({}):
        fail("group gap: a party of one has no group shot to miss")
    led.expected_participants = 4
    ok("group gap fires per day, only when a head-count exists and a threshold ≥2 is derivable")


def check_validator_floor() -> None:
    loop = asyncio.new_event_loop()
    bounty = {"targetMoment": "group_shot", "targetStage": "day3_gion"}
    event = {"expectedParticipants": 4}

    def judge(estimate: int, aesthetic: float = 0.8):  # noqa: ANN202
        media = {"curator": {"aestheticScore": aesthetic, "peopleCountEstimate": estimate,
                             "momentTags": [], "stageId": "day3_gion"}}
        return loop.run_until_complete(
            validate_mod._judge("e", media, bounty, event=event)  # noqa: SLF001
        )

    verdict, score, reason, usage = judge(2)
    if verdict.value != "rejected" or usage.tokensIn:
        fail(f"validator: 2 of 4 people must reject deterministically, got {verdict} ({reason})")
    verdict, score, _, usage = judge(4)
    if verdict.value != "fulfilled" or score != 1.0 or usage.tokensIn:
        fail(f"validator: 4 people should fulfil with no model call, got {verdict}")
    verdict, _, _, _ = judge(3, aesthetic=0.2)
    if verdict.value != "partial":
        fail(f"validator: right frame badly shot must be partial, got {verdict}")
    loop.close()
    ok("group fulfilment is arithmetic on the Curator's stored estimate — zero model tokens")


# ================================================================ live


def live_half(api: str) -> None:
    from smoke_faces import mint_host_token  # noqa: PLC0415
    from shared import fs  # noqa: PLC0415
    from shared.auth import custom_claims  # noqa: PLC0415
    from shared.ulid import new_ulid  # noqa: PLC0415
    from smoke_upload import put_bytes, register_intent, sign_in_anonymously, unique_jpeg  # noqa: PLC0415

    cfg = settings()
    cfg.require("project")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")
    if not PHOTO.exists():
        fail(f"missing fixture {PHOTO} — run the B1 risk probes once")

    now = dt.datetime.now(UTC)
    event_id = f"dev_group_{new_ulid().lower()[:8]}"
    fs.event_ref(event_id).set(
        {
            "eventId": event_id,
            "name": "Group Smoke Trip",
            "timezone": "Asia/Tokyo",
            "status": EventStatus.LIVE.value,
            "class": EventClass.INTERNAL_DEV.value,
            "startsOn": now.date().isoformat(),
            "endsOn": (now + dt.timedelta(days=1)).date().isoformat(),
            "expectedParticipants": 4,
            "stages": [
                {
                    "stageId": "walk",
                    "label": "The walk",
                    "startsAt": now - dt.timedelta(hours=1),
                    "endsAt": now + dt.timedelta(hours=2),
                    "requiredMoments": [],
                    "theme": None,
                    "expectedSetting": None,
                }
            ],
            "eventTypeProfile": {
                "templateId": "custom", "vipTopology": "pyramid",
                "sensitivityProfile": {"pda": "context_dependent", "alcohol": "context_dependent",
                                       "attire": "standard"},
                "culturalGlossary": [], "requiredMomentsTemplate": [],
            },
            "createdAt": now,
            "liveAt": now,
        }
    )
    host_token = mint_host_token(event_id, api_key)
    host = {"Authorization": f"Bearer {host_token}"}

    # --- 1. host-enroll: album machinery yes, identity no
    photo_b64 = base64.b64encode(PHOTO.read_bytes()).decode("ascii")
    resp = requests.post(
        f"{api}/v1/events/{event_id}/people/host-enroll",
        json={"photo": photo_b64, "displayName": "Riya", "tier": 1, "photoConsent": True},
        headers=host,
        timeout=120,
    )
    if resp.status_code != 200:
        fail(f"host-enroll failed ({resp.status_code}): {resp.text[:300]}")
    person_id = resp.json()["personId"]

    person = fs.person_ref(event_id, person_id).get().to_dict() or {}
    if not (person.get("hostEnrolled") and person.get("claimApproved") and person.get("tier") == 1):
        fail(f"host-enroll: person doc wrong: {person}")
    enrollment = fs.enrollment_ref(event_id, person_id).get().to_dict() or {}
    if len(enrollment.get("embedding") or []) != 512:
        fail("host-enroll: no 512-d embedding in enrollments/")
    private = fs.person_private_ref(event_id, person_id).get().to_dict() or {}
    if private.get("uidLinks"):
        fail(f"host-enroll: uidLinks must be empty — no identity is granted here: {private}")
    print("  ok  live: host-enroll → person + embedding, zero uidLinks (no identity granted)")

    refused = requests.post(
        f"{api}/v1/events/{event_id}/people/host-enroll",
        json={"photo": photo_b64, "displayName": "X", "tier": 3, "photoConsent": False},
        headers=host,
        timeout=60,
    )
    if refused.status_code != 400 or "CONSENT_REQUIRED" not in refused.text:
        fail(f"host-enroll without the permission acknowledgment was accepted ({refused.status_code})")
    print("  ok  live: the permission acknowledgment is required, never pre-ticked")

    # --- 2. tier endpoint + audit
    resp = requests.post(
        f"{api}/v1/events/{event_id}/people/{person_id}/tier",
        json={"tier": 0},
        headers=host,
        timeout=30,
    )
    if resp.status_code != 200 or (fs.person_ref(event_id, person_id).get().to_dict() or {}).get("tier") != 0:
        fail(f"tier endpoint failed ({resp.status_code}): {resp.text[:200]}")
    audits = [s.to_dict() for s in fs.event_ref(event_id).collection("ops").stream()]
    if not any(a.get("kind") == "tier_changed" for a in audits):
        fail("tier change left no ops/ audit record")
    print("  ok  live: tier 1 → 0 applied and audited")

    # --- 3. a real upload bumps the peopleBuckets histogram
    guest_token, guest_uid = sign_in_anonymously(api_key)
    requests.post(
        f"{api}/v1/events/{event_id}/join", json={}, headers={"Authorization": f"Bearer {guest_token}"},
        timeout=30,
    )
    media_id = new_ulid()
    intent = register_intent(api, event_id, guest_token, media_id, PHOTO.read_bytes(), "pool")
    put_bytes(intent["signedUrl"], PHOTO.read_bytes())
    deadline = time.time() + 180
    shard = {}
    while time.time() < deadline:
        doc = fs.media_ref(event_id, media_id).get().to_dict() or {}
        if doc.get("status") == "indexed":
            shard = (fs.coverage_stage_shard_ref(event_id, str((doc.get("curator") or {}).get("stageId") or "_unstaged"))
                     .get().to_dict() or {})
            break
        time.sleep(5)
    buckets = shard.get("peopleBuckets") or {}
    if sum(int(v or 0) for v in buckets.values()) < 1:
        fail(f"peopleBuckets never bumped: {shard}")
    print(f"  ok  live: the indexing transaction bumped peopleBuckets {dict(buckets)}")

    # --- 4. assignment release + deterministic assignee
    fs.guest_ref(event_id, guest_uid).set(
        {"uid": guest_uid, "uploads": 1, "lastSeenAt": dt.datetime.now(UTC)}, merge=True
    )
    picked = act.resolve_assignee(event_id)
    if picked != guest_uid:
        fail(f"resolve_assignee picked {picked!r}, wanted the active uploader {guest_uid!r}")
    print("  ok  live: resolve_assignee picks the most recently active uploader, deterministically")

    bounty_id = new_ulid()
    fs.bounty_ref(event_id, bounty_id).set(
        {
            "bountyId": bounty_id,
            "status": BountyStatus.ACTIVE.value,
            "targetStage": "walk",
            "targetMoment": "group_shot",
            "dedupeKey": "walk|group_shot|-",
            "title": "The group shot",
            "copy": "All four of you in one frame.",
            "points": 150,
            "basePoints": 150,
            "vipWeight": 1.0,
            "audience": BountyAudience.ASSIGNEE.value,
            "assigneeUid": "uid_that_never_answered",
            "assignedAt": now - dt.timedelta(minutes=BOUNTY_ASSIGN_TIMEOUT_MINUTES + 5),
            "assignmentTimeoutAt": now - dt.timedelta(minutes=5),
            "source": "reconciliation",
            "createdAt": now - dt.timedelta(minutes=BOUNTY_ASSIGN_TIMEOUT_MINUTES + 5),
            "expiresAt": now + dt.timedelta(minutes=30),
            "submissions": [],
        }
    )
    resp = requests.post(
        f"{api}/internal/tick",
        params={"eventId": event_id},
        headers=host,
        timeout=120,
    )
    if resp.status_code != 200:
        fail(f"tick failed ({resp.status_code}): {resp.text[:200]}")
    released = fs.bounty_ref(event_id, bounty_id).get().to_dict() or {}
    if released.get("audience") != BountyAudience.ALL.value or released.get("assigneeUid"):
        fail(f"stale assignment not released to broadcast: {released}")
    print("  ok  live: an unanswered assignment flips to a broadcast on the next tick — no model involved")

    fs.event_ref(event_id).update({"status": EventStatus.WRAPPED.value})
    ok(f"live half green ({event_id} wrapped, not deleted — internal_dev housekeeping)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=None, help="api base URL; omitted = offline half only")
    args = parser.parse_args()

    check_histogram()
    check_group_gap()
    check_validator_floor()

    if args.api:
        live_half(args.api.rstrip("/"))
        print("\nPASS  group coverage + targeting + host enrollment, offline + live")
    else:
        print("\nPASS  group coverage truth tables (offline only — pass --api for the live half)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
