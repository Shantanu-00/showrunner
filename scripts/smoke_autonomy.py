"""End-to-end smoke test of the autonomy spine: Cloud Scheduler → tick → publisher → the wall.

Companion to `smoke_upload.py` (spine + Curator), `smoke_faces.py` (identity) and `smoke_safety.py`
(Guardian + indexing). Those three prove the *data* plane. This one proves the *control* plane, which
is the 40%-criterion claim — "intercept and complete a multi-step background workflow without human
intervention" — so every assertion below is written to be falsifiable rather than reassuring:

  1. **The program is a decision table, not a prompt.** `--program-only` runs the hero score and
     spec 04 §6's diversity criterion (no face cluster twice in any five consecutive hero slots,
     *including across the loop boundary*) against fixtures with no network and no spend. Same shape
     as `smoke_safety.py --gate-only`, and for the same reason: the ranking that decides what a room
     full of people looks at should be checkable without a cloud account.
  2. **The Scheduler fires with nobody pressing anything.** The script reads `platform/tickPulse`,
     then *waits* — it does not trigger the job. If the heartbeat advances, the fleet ticked on its
     own; if it does not, the claim is false and this exits non-zero.
  3. **The 30-second demo cadence is real and server-side.** Spec 09 §2 delivers it as a `* * * * *`
     job plus a +30 s Cloud Task interleave, so the proof is a *gap* of ~30 s between consecutive
     heartbeats — something a 1-minute cron alone cannot produce.
  4. **A photo reaches the wall unprompted.** A two-photo Ring-2 batch goes through the real upload
     path; the lead photo takes the priority lane; the publisher's listener notices and writes
     `kiosk/playlist`. The measured phone→wall latency is printed, not asserted into a slogan.
  5. **One writer, and no pointless revisions.** The playlist names the lease holder that wrote it,
     and a second *rebuild* over unchanged inputs reports `unchanged` without bumping `revision` —
     which matters because the kiosk client restarts the show on every revision it sees. Asserted by
     nudging the publisher twice rather than by ticking twice: since S8b a tick is not read-only, and
     a director that escalates a bounty onto the wall between two ticks has changed the program on
     purpose (see `check_fingerprint_guard`).

    python scripts/smoke_autonomy.py --program-only              # no network, no spend
    python scripts/smoke_autonomy.py --event-id dev_...          # the full ~4 minute run
    python scripts/smoke_autonomy.py --event-id dev_... --skip-cadence   # drop the 100 s sampling
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402

from publisher import program  # noqa: E402
from shared import fs, internal as face_internal  # noqa: E402
from shared.settings import (  # noqa: E402
    DEMO_INTERLEAVE_SECONDS,
    KIOSK_DIVERSITY_WINDOW,
    KIOSK_JUST_IN_WINDOW_SEC,
    settings,
)
from shared.ulid import new_ulid  # noqa: E402

from smoke_faces import mint_host_token  # noqa: E402
from smoke_upload import put_bytes, sign_in_anonymously, unique_jpeg  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: The AI-generated cast portrait from the B1 probes (a real face, no real guest — video content
#: rules). It has to be this fixture rather than a synthetic gradient: a gradient scores 0.0 through
#: the Curator, and `recompute_visibility` correctly keeps a sub-floor photo out of every public
#: surface, so it would never reach the wall and this test would be measuring the aesthetic floor
#: instead of the publisher.
PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ---------------------------------------------------------------- 1. the program, offline


def _candidate(
    media_id: str,
    *,
    aesthetic: float,
    age_min: float,
    keys: set[str],
    vip: float = 1.0,
    stage: str | None = "sangeet",
    now: dt.datetime,
) -> program.Candidate:
    at = now - dt.timedelta(minutes=age_min)
    return program.Candidate(
        media_id=media_id,
        aesthetic=aesthetic,
        captured_at=at,
        uploaded_at=at,
        stage_id=stage,
        dedupe_keys=frozenset(keys),
        vip_weight=vip,
    )


def _hero_ids(built: program.Program) -> list[str]:
    return [s["mediaId"] for s in built.slots if s["type"] == "hero"]


def check_program() -> None:
    """The ranking rules, as a table. Every row is a sentence from spec 04 §4 or spec 11 §3.3."""
    now = dt.datetime.now(dt.timezone.utc)
    W = KIOSK_DIVERSITY_WINDOW

    # --- the factors, one at a time, each with everything else held equal
    rows: list[tuple[str, list[program.Candidate], list[str]]] = [
        (
            "vipWeight: a tier-0 Principal outranks a better-looking guest shot (×3.0)",
            [
                _candidate("guest", aesthetic=0.90, age_min=1, keys={"face:a"}, now=now),
                _candidate("bride", aesthetic=0.40, age_min=1, keys={"face:b"}, vip=3.0, now=now),
            ],
            ["bride", "guest"],
        ),
        (
            "stageMatch: the wall follows the event (active ×1.0 beats previous ×0.4)",
            [
                _candidate(
                    "earlier", aesthetic=0.80, age_min=1, keys={"face:a"}, stage="haldi", now=now
                ),
                _candidate("current", aesthetic=0.50, age_min=1, keys={"face:b"}, now=now),
            ],
            ["current", "earlier"],
        ),
        (
            "recency: a 40-minute-old photo has decayed two half-lives behind a fresh equal",
            [
                _candidate("old", aesthetic=0.80, age_min=40, keys={"face:a"}, now=now),
                _candidate("fresh", aesthetic=0.30, age_min=0, keys={"face:b"}, now=now),
            ],
            ["fresh", "old"],
        ),
        (
            "diversity: a second shot of the same cluster loses to a weaker, different face",
            [
                _candidate("groom_1", aesthetic=0.90, age_min=1, keys={"face:g"}, now=now),
                _candidate("groom_2", aesthetic=0.85, age_min=1, keys={"face:g"}, now=now),
                _candidate("aunt", aesthetic=0.50, age_min=1, keys={"face:x"}, now=now),
            ],
            ["groom_1", "aunt", "groom_2"],
        ),
    ]
    for label, candidates, expected in rows:
        built = program.build(
            candidates, now=now, active_stage_id="sangeet", previous_stage_id="haldi"
        )
        got = _hero_ids(built)
        if got != expected:
            fail(f"program: {label} → {got}, expected {expected}")
        print(f"  ok  {' → '.join(got):<34} {label}")

    # --- spec 04 §6: no face cluster twice in any five consecutive hero slots, loop included.
    #
    # The criterion is arithmetic before it is code: five consecutive distinct clusters need at least
    # five distinct clusters *with depth to spare*. At exactly five, satisfying it over 18 slots would
    # require the pool to divide perfectly — the last few slots have no unused cluster left to reach
    # for. So the guarantee is asserted from six distinct clusters up, and the row below asserts what
    # happens *below* the threshold instead of pretending the threshold is not there.
    for distinct in (W + 1, W + 3, 12):
        candidates = [
            _candidate(
                f"M{i}", aesthetic=0.4 + (i % 6) / 10, age_min=i * 2, keys={f"face:p{i % distinct}"}, now=now
            )
            for i in range(18)
        ]
        built = program.build(candidates, now=now, active_stage_id="sangeet")
        heroes = _hero_ids(built)
        by_id = {c.media_id: c for c in candidates}
        collisions = [
            i
            for i in range(len(heroes))
            if any(
                by_id[heroes[i]].dedupe_keys & by_id[heroes[(i - k) % len(heroes)]].dedupe_keys
                for k in range(1, W)
                if k < len(heroes)
            )
        ]
        if collisions:
            fail(
                f"program: {distinct} distinct clusters over {len(heroes)} hero slots left "
                f"collisions at {collisions} (spec 04 §6)"
            )
    ok(f"diversity: no cluster repeats inside {W} consecutive hero slots, loop boundary included")

    # --- below the threshold: degrade by repeating, never by going dark
    scarce = [
        _candidate(f"S{i}", aesthetic=0.5 + i / 20, age_min=i, keys={f"face:p{i % 2}"}, now=now)
        for i in range(6)
    ]
    scarce_heroes = _hero_ids(program.build(scarce, now=now))
    if len(scarce_heroes) != len(scarce):
        fail(
            f"only {len(scarce_heroes)} of {len(scarce)} photos reached the wall — with two clusters "
            "the rule is unsatisfiable, and dropping photos is the wrong way to fail"
        )
    single = [_candidate("only", aesthetic=0.7, age_min=1, keys={"face:a"}, now=now)]
    if _hero_ids(program.build(single, now=now)) != ["only"]:
        fail("program: one photo with one face produced no hero slot — a five-guest party goes dark")
    ok("scarcity: 2 clusters over 6 photos still fills 6 slots; 1 photo still makes a program")

    # --- ordering of the takeovers, and the just_in guarantee
    built = program.build(
        [_candidate("a", aesthetic=0.9, age_min=0, keys={"face:a"}, now=now)],
        now=now,
        premiere_reel_id="R1",
        takeover_bounty_id="B1",
    )
    types = [s["type"] for s in built.slots]
    if types[:4] != ["reel", "bounty_call", "just_in", "hero"]:
        fail(f"program: takeover order is {types[:4]}, expected reel → bounty_call → just_in → hero")
    ok("takeovers lead: a reel premiere, then an escalated bounty, then the just-in strip")

    stale = program.build(
        [_candidate("a", aesthetic=0.9, age_min=30, keys={"face:a"}, now=now)], now=now
    )
    if any(s["type"] == "just_in" for s in stale.slots[:1]):
        fail("program: just_in led the program with nothing uploaded in the last two minutes")
    ok("just_in leads only while something is actually new (liveWindowSec 120)")

    if program.build([], now=now).slots:
        fail("program: an event with nothing public produced slots — the client owns the pre-show")
    ok("an event with nothing public gets an empty program, not a shimmering placeholder")

    # --- the fingerprint: what decides whether a rebuild earns a revision
    one = [_candidate("a", aesthetic=0.9, age_min=5, keys={"face:a"}, now=now)]
    later = program.build(one, now=now + dt.timedelta(seconds=45))
    if program.build(one, now=now).fingerprint != later.fingerprint:
        fail("fingerprint: recency drift alone changed it — the show would restart every rebuild")
    two = one + [_candidate("b", aesthetic=0.5, age_min=1, keys={"face:b"}, now=now)]
    if program.build(one, now=now).fingerprint == program.build(two, now=now).fingerprint:
        fail("fingerprint: a new public photo did not change it — the wall would never update")
    ok("fingerprint: stable under score drift, changes when the program does")


# ---------------------------------------------------------------- helpers


def scheduler_jobs() -> dict[str, dict[str, Any]]:
    """Read the two jobs through gcloud. Returns {} when gcloud is unavailable, rather than failing:
    the console page is the evidence surface, this is a convenience check on top of it."""
    cfg = settings()
    found: dict[str, dict[str, Any]] = {}
    for name in ("director-tick", "director-tick-demo"):
        cmd = (
            f"gcloud scheduler jobs describe {name} --location {cfg.location} "
            f"--project {cfg.project} --format=json"
        )
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            print(f"      (gcloud unavailable: {exc})")
            return {}
        if proc.returncode != 0:
            print(f"      (describe {name} failed: {proc.stderr.strip()[:160]})")
            continue
        try:
            found[name] = json.loads(proc.stdout)
        except ValueError:
            continue
    return found


def pulse() -> dict[str, Any]:
    snap = fs.platform_doc("tickPulse").get()
    return (snap.to_dict() or {}) if snap.exists else {}


def playlist(event_id: str) -> dict[str, Any]:
    snap = fs.kiosk_playlist_ref(event_id).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def register_batch(
    api: str, event_id: str, token: str, files: list[tuple[str, bytes]], ring: str
) -> list[dict[str, Any]]:
    """One /uploads call carrying several files — the shape the priority lane is about."""
    resp = requests.post(
        f"{api}/v1/events/{event_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "batchId": new_ulid(),
            "consent": {"public": ring == "public", "selfOnly": ring == "self"},
            "files": [
                {
                    "clientMediaId": media_id,
                    "fileName": f"{media_id}.jpg",
                    "contentType": "image/jpeg",
                    "size": len(data),
                }
                for media_id, data in files
            ],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        fail(f"POST /uploads failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()["uploads"]


def wait_until(label: str, predicate, timeout: float, poll: float = 2.0) -> Any:
    """Poll `predicate` until it returns something truthy. Returns it, or fails.

    A transient Firestore error is swallowed and retried rather than allowed to end the run: this
    script polls for minutes at a time from a dev box, the SDK's own retry budget is 300 s (longer
    than most of the waits here), and a dropped gRPC channel on a flaky home network says nothing
    about whether the fleet is ticking. A failure that persists past the timeout still fails the run.
    """
    started = time.time()
    last_error: str | None = None
    while time.time() - started < timeout:
        try:
            value = predicate()
        except Exception as exc:  # noqa: BLE001 - see docstring: the network is not under test
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"      (transient: {last_error[:120]})")
            time.sleep(poll)
            continue
        if value:
            return value
        time.sleep(poll)
    suffix = f" (last error: {last_error})" if last_error else ""
    fail(f"timed out after {timeout:.0f}s waiting for {label}{suffix}")
    return None


# ---------------------------------------------------------------- 2. the live spine


def check_scheduler() -> None:
    jobs = scheduler_jobs()
    if not jobs:
        print("      SKIP  could not read Cloud Scheduler — check the console page instead")
        return
    expected = {"director-tick": "*/2 * * * *", "director-tick-demo": "* * * * *"}
    for name, schedule in expected.items():
        job = jobs.get(name)
        if job is None:
            fail(f"Cloud Scheduler job {name} does not exist — run ./deploy/scheduler.sh")
        if job.get("schedule") != schedule:
            fail(f"{name} schedule is {job.get('schedule')!r}, expected {schedule!r} (spec 09 §2)")
        state = job.get("state")
        if state != "ENABLED":
            fail(f"{name} is {state} — a paused heartbeat is not autonomy")
        target = (job.get("httpTarget") or {}).get("uri", "")
        oidc = ((job.get("httpTarget") or {}).get("oidcToken") or {}).get("serviceAccountEmail", "")
        if "/internal/tick" not in target:
            fail(f"{name} points at {target!r}, not /internal/tick")
        if not oidc:
            fail(f"{name} carries no OIDC token — /internal/tick would reject it, and should")
        last = job.get("lastAttemptTime") or "never"
        ok(f"{name}: {schedule} → {target.rsplit('/', 1)[-1]} as {oidc.split('@')[0]} · last {last}")


def check_unprompted_tick(timeout: float) -> dict[str, Any]:
    """Wait — do not trigger. The point of the test is that nothing has to be pressed."""
    before = pulse()
    baseline = int(before.get("ticks") or 0)
    if before:
        print(f"      baseline: ticks={baseline} lastTickAt={before.get('lastTickAt')}")
    else:
        print("      baseline: no heartbeat yet (first tick will create it)")
    started = time.time()
    after = wait_until(
        "the Scheduler to fire on its own",
        lambda: (lambda p: p if int(p.get("ticks") or 0) > baseline else None)(pulse()),
        timeout,
    )
    waited = time.time() - started
    ok(
        f"a tick ran with nobody pressing anything: ticks {baseline} → {after.get('ticks')} "
        f"after {waited:.0f}s (mode={after.get('mode')}, caller={after.get('caller')}, "
        f"events={after.get('events')}, ms={after.get('ms')})"
    )
    return after


def check_interleave(window: float) -> None:
    """The +30 s Cloud Task, proven by a gap a 1-minute cron cannot produce (spec 09 §2/§5)."""
    print(f"      sampling the heartbeat for {window:.0f}s — a 30 s gap is the interleave")
    seen: dict[str, dt.datetime] = {}
    started = time.time()
    while time.time() - started < window:
        try:
            doc = pulse()
        except Exception as exc:  # noqa: BLE001 - same reasoning as wait_until
            print(f"        (transient: {type(exc).__name__})")
            time.sleep(2.0)
            continue
        tick_at, tick_id = doc.get("lastTickAt"), doc.get("lastTickId")
        key = str(tick_id or tick_at)
        if isinstance(tick_at, dt.datetime) and key not in seen:
            seen[key] = tick_at
            print(
                f"        t+{time.time() - started:5.1f}s  {tick_at:%H:%M:%S}  "
                f"mode={doc.get('mode')} interleave={bool(doc.get('interleave'))}"
            )
        time.sleep(1.5)

    stamps = sorted(seen.values())
    if len(stamps) < 2:
        fail(f"only {len(stamps)} tick(s) in {window:.0f}s — the heartbeat is not running")
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    tight = [g for g in gaps if 15.0 <= g <= 45.0]
    print(f"      gaps: {', '.join(f'{g:.0f}s' for g in gaps)}")
    if not tight:
        fail(
            f"no gap near {DEMO_INTERLEAVE_SECONDS}s in {gaps} — the demo cadence is running at the "
            "cron floor, so the +30 s Cloud Task interleave is not landing"
        )
    ok(
        f"{len(stamps)} ticks in {window:.0f}s with a {min(tight):.0f}s gap — the interleave is real "
        f"(Scheduler's cron floor is 60 s, so this cadence cannot come from cron alone)"
    )


def check_batch_lead(event_id: str, lead: str, tail: str) -> None:
    docs = {mid: (fs.media_ref(event_id, mid).get().to_dict() or {}) for mid in (lead, tail)}
    if not docs[lead].get("batchLead"):
        fail(f"{lead} is the first file of the batch but batchLead is {docs[lead].get('batchLead')!r}")
    if docs[tail].get("batchLead"):
        fail(f"{tail} is not the lead but batchLead is set — every photo would jump the queue")
    ok("batchLead: exactly the first file of the batch takes the priority classify lane (§7d)")


def check_wall(event_id: str, media_id: str, uploaded_at: float, timeout: float) -> dict[str, Any]:
    """The milestone: an uploaded photo appears in `kiosk/playlist` with nobody pressing anything."""
    before = playlist(event_id)
    before_revision = int(before.get("revision") or 0)

    doc = wait_until(
        "the photo to reach status=indexed",
        lambda: (
            lambda d: d if d.get("status") == "indexed" else None
        )(fs.media_ref(event_id, media_id).get().to_dict() or {}),
        timeout,
    )
    indexed_at = time.time()
    if doc.get("visibility") != "public":  # noqa: SIM102 - the diagnosis below is the point
        fail(
            f"visibility={doc.get('visibility')!r} — a Ring-2 photo that is not public cannot reach "
            f"the wall (guardian={(doc.get('guardian') or {}).get('verdict')}, "
            f"aesthetic={(doc.get('curator') or {}).get('aestheticScore')})"
        )
    ok(f"pipeline: status=indexed, visibility=public after {indexed_at - uploaded_at:.1f}s")

    after = wait_until(
        "the publisher to put it on the wall",
        lambda: (
            lambda p: p
            if int(p.get("revision") or 0) > before_revision
            and media_id in [s.get("mediaId") for s in (p.get("slots") or [])]
            else None
        )(playlist(event_id)),
        timeout,
    )
    on_wall = time.time()
    heroes = [s for s in after.get("slots") or [] if s.get("type") == "hero"]
    ok(
        f"kiosk/playlist revision {before_revision} → {after.get('revision')} carries {media_id} "
        f"({len(heroes)} hero slots, trigger={after.get('trigger')!r})"
    )
    # Report the parts separately, because they have different owners and only two of them are the
    # system's: the bytes leaving this dev box over a home uplink are not a pipeline latency, and a
    # cold `worker-curate`/`worker-safety` (min-instances 0 by design, spec 09 §1) is what the
    # warm-up runbook in spec 09 §5 exists to remove before anyone films anything.
    stamps = {k: doc.get(k) for k in ("uploadedAt", "indexedAt")}
    if all(isinstance(v, dt.datetime) for v in stamps.values()):
        pipeline = (stamps["indexedAt"] - stamps["uploadedAt"]).total_seconds()
        client = (stamps["uploadedAt"].timestamp() - uploaded_at) if uploaded_at else 0.0
        print(
            f"      phone → wall: {client:.1f}s client upload + {pipeline:.1f}s pipeline "
            f"(uploadedAt → indexed) + {on_wall - indexed_at:.1f}s publisher"
        )
        print("      cold workers dominate a first run — spec 09 §5's warm-up runbook is the fix")
    else:
        print(f"      phone → wall: {on_wall - uploaded_at:.1f}s total")
    return after


def check_playlist_shape(event_id: str, doc: dict[str, Any]) -> None:
    """Everything on the wall must be something the wall is allowed to fetch."""
    slots = doc.get("slots") or []
    if not slots:
        fail("the playlist has no slots")

    hero_ids = [s.get("mediaId") for s in slots if s.get("type") == "hero"]
    for media_id in hero_ids:
        media = fs.media_ref(event_id, str(media_id)).get().to_dict() or {}
        if media.get("visibility") != "public" or media.get("status") != "indexed":
            fail(
                f"hero slot {media_id} is visibility={media.get('visibility')!r} "
                f"status={media.get('status')!r} — the kiosk's own read rule would deny it"
            )
    ok(f"every one of the {len(hero_ids)} hero slots is public + indexed (spec 04 §2)")

    factors = next((s.get("factors") for s in slots if s.get("type") == "hero"), None)
    required = {"aesthetic", "recency", "diversity", "stageMatch", "vipWeight", "rank"}
    if not factors or not required.issubset(factors):
        fail(f"hero factors are {factors!r} — the 'Why this photo?' card has nothing to render")
    ok(f"stored ranking factors: {' '.join(f'{k}={factors[k]}' for k in sorted(required))}")

    lease = fs.publisher_lease_ref(event_id).get().to_dict() or {}
    if not lease.get("held") or not lease.get("holder"):
        fail(f"publisherLease/{event_id} is {lease!r} — nothing claims to own this wall")
    if doc.get("publishedBy") != lease.get("holder"):
        fail(
            f"the playlist was written by {doc.get('publishedBy')!r} but the lease is held by "
            f"{lease.get('holder')!r} — two writers"
        )
    ok(f"single writer: publisherLease held by {lease['holder']}, and it wrote this revision")


def check_stage_retheme(event_id: str, event: dict[str, Any], timeout: float = 30.0) -> None:
    """Spec 04 §6: a stage change re-themes the kiosk in ≤ 5 s.

    Driven by the host's `stageOverride` (spec 05 §2: "the big Now: ▶ Pheras button always wins
    instantly"), which is the only input to the wall that no photo triggers — so this is the one check
    that exercises the publisher's *event-document* listener rather than its media queries. The
    override is removed again in `finally`: leaving one behind would pin a dev event's wall to a stage
    the schedule disagrees with, and the next session would be debugging the wrong thing.
    """
    stages = [s.get("stageId") for s in (event.get("stages") or []) if s.get("stageId")]
    current = event.get("stageOverride") or event.get("activeStage")
    target = next((s for s in stages if s != current), None)
    if target is None:
        print("      SKIP  the event has fewer than two stages, so there is nothing to switch to")
        return

    themes = {s.get("stageId"): s.get("theme") for s in (event.get("stages") or [])}
    started = time.time()
    try:
        fs.event_ref(event_id).update({"stageOverride": target})
        after = wait_until(
            f"the wall to follow the stage change to {target}",
            lambda: (lambda p: p if p.get("activeStageId") == target else None)(playlist(event_id)),
            timeout,
            poll=1.0,
        )
        elapsed = time.time() - started
        if after.get("theme") != themes.get(target):
            fail(
                f"the wall moved to {target} but kept theme {after.get('theme')!r}, expected "
                f"{themes.get(target)!r} — the per-stage palette comes with the stage (spec 04 §4)"
            )
        ok(
            f"stage override {current} → {target} re-themed the wall to {after.get('theme')!r} in "
            f"{elapsed:.1f}s (spec 04 §6 allows 5 s; this path touches no photo at all)"
        )
    finally:
        fs.event_ref(event_id).update({"stageOverride": fs.DELETE_FIELD})
        wait_until(
            "the wall to return to the scheduled stage",
            lambda: (lambda p: p if p.get("activeStageId") == current else None)(playlist(event_id)),
            timeout,
            poll=1.0,
        )
        print(f"      restored: stageOverride cleared, wall back on {current}")


def _one_tick(api: str, host_token: str, event_id: str, label: str) -> str | None:
    resp = requests.post(
        f"{api}/internal/tick",
        headers={"Authorization": f"Bearer {host_token}"},
        params={"eventId": event_id},
        timeout=180,
    )
    if resp.status_code != 200:
        fail(f"host-triggered tick ({label}) failed ({resp.status_code}): {resp.text[:300]}")
    ticked = resp.json().get("ticked") or []
    if len(ticked) != 1:
        fail(f"host tick ({label}) ticked {len(ticked)} events, expected exactly 1: {resp.json()}")
    return (ticked[0].get("publisher") or {}).get("status")


def check_host_tick(api: str, host_token: str, event_id: str) -> None:
    """Spec 05 §1's host fallback button: it works, and it ticks exactly the one event it named."""
    outcomes = [_one_tick(api, host_token, event_id, f"host #{n}") for n in (1, 2)]
    ok(f"host fallback tick works and is event-scoped: publisher said {outcomes}")


def check_fingerprint_guard(event_id: str, uploaded_at: float) -> None:
    """The publisher writes a revision only when the *program* changed (S8a's change-detection).

    This is asserted by nudging the publisher twice directly rather than by ticking twice, and the
    distinction is the whole point: **since S8b a tick is not a read-only operation.** It can issue a
    bounty, escalate one onto the wall as a `bounty_call` takeover, expire one, post an announcement or
    advance a stage — every one of which is an input to the program, so two consecutive ticks *should*
    sometimes produce two revisions. Measured live the first time this ran after the director landed:
    the director escalated a bounty between the two ticks, `bounty_call` correctly took the lead slot,
    and the old assertion reported the fingerprint guard as broken when the guard had worked perfectly.

    The property that actually matters — "a rebuild over unchanged inputs must be *absent*, not merely
    idempotent, because the kiosk client restarts the show on every revision it sees" — belongs to the
    publisher and is tested here with nothing else in the loop.

    The wait is not padding either: `just_in` leads only while something was uploaded inside the last
    `KIOSK_JUST_IN_WINDOW_SEC` (spec 04 §4), so for two minutes after this script's own upload the
    program legitimately changes on its own and "unchanged inputs" is not yet a true statement.
    """
    remaining = KIOSK_JUST_IN_WINDOW_SEC + 10 - (time.time() - uploaded_at)
    if remaining > 0:
        print(
            f"      waiting {remaining:.0f}s for the just_in window to close: until it does the "
            "program changes on its own, and 'unchanged inputs' is not yet true"
        )
        time.sleep(remaining)

    first = face_internal.nudge_publisher(event_id, reason="smoke:settle")
    time.sleep(2.0)
    before = int(playlist(event_id).get("revision") or 0)
    second = face_internal.nudge_publisher(event_id, reason="smoke:measure")
    time.sleep(2.0)
    after = int(playlist(event_id).get("revision") or 0)

    if second.get("status") != "unchanged" or after != before:
        current = playlist(event_id)
        fail(
            f"a rebuild over unchanged inputs reported {second.get('status')!r} and moved revision "
            f"{before} → {after} (trigger={current.get('trigger')!r}, "
            f"slots={[s.get('type') for s in current.get('slots') or []][:3]}); the kiosk restarts its "
            "program on every revision, so an unchanged rebuild has to write nothing at all"
        )
    ok(
        f"two rebuilds, one revision: the second reported {second.get('status')!r} and left revision "
        f"at {after} (first said {first.get('status')!r}) — the fingerprint guard holds"
    )


def check_tick_lease(event_id: str) -> None:
    lease = fs.tick_ref(event_id).get().to_dict() or {}
    if not lease:
        fail(f"ticks/{event_id} does not exist — no tick ever took a lease on this event")
    if lease.get("held"):
        fail(
            f"ticks/{event_id} is still held by {lease.get('holder')!r}: a tick that keeps its lease "
            "for the full TTL would block the next scheduled tick instead of protecting it"
        )
    if lease.get("lastOutcome") != "ok":
        fail(f"the last tick on {event_id} ended {lease.get('lastOutcome')!r}")
    ok(
        f"tick lease released after use: {lease.get('acquisitions')} acquisitions, "
        f"last {lease.get('lastTrigger')} tick ok at {lease.get('lastTickAt')}"
    )


# ---------------------------------------------------------------- main


def main() -> int:
    cfg = settings()
    ap = argparse.ArgumentParser(description="Smoke-test the autonomy spine (S8a).")
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--program-only", action="store_true", help="the offline table only")
    ap.add_argument("--skip-cadence", action="store_true", help="skip the 100 s interleave sampling")
    ap.add_argument("--timeout", type=float, default=150.0)
    args = ap.parse_args()

    print("── the program builder (spec 04 §4/§6, spec 11 §3.3) — pure, offline, no spend")
    check_program()
    if args.program_only:
        print("\nPASS  program decision table only (--program-only)")
        return 0

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not args.event_id:
        fail("no event — pass --event-id or set SMOKE_EVENT_ID (see scripts/dev_event.py)")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")

    event = fs.get_event(args.event_id)
    if not event:
        fail(f"event {args.event_id} does not exist")
    if event.get("status") not in ("live", "wrapping"):
        fail(
            f"event {args.event_id} is {event.get('status')!r} — the tick and the publisher both only "
            "touch live/wrapping events, by design"
        )
    print(f"\n── Cloud Scheduler (spec 09 §2) · project {cfg.project}")
    check_scheduler()

    print("\n── the heartbeat: does the fleet act without being asked?")
    check_unprompted_tick(timeout=180.0)
    if not args.skip_cadence:
        check_interleave(window=100.0)
    else:
        print("      SKIP  --skip-cadence: the +30 s interleave was not measured")

    print(f"\n── phone → wall, on the real path (event {args.event_id})")
    if not PHOTO.exists():
        fail(f"fixture missing: {PHOTO} (run scripts/risk_tests/banana.py to regenerate the cast)")
    token, _uid = sign_in_anonymously(api_key)
    lead_id, tail_id = new_ulid(), new_ulid()
    lead_bytes, tail_bytes = unique_jpeg(PHOTO), unique_jpeg(PHOTO)
    targets = register_batch(
        api, args.event_id, token, [(lead_id, lead_bytes), (tail_id, tail_bytes)], "public"
    )
    check_batch_lead(args.event_id, lead_id, tail_id)

    uploaded_at = time.time()
    for target, data in zip(targets, (lead_bytes, tail_bytes)):
        put_bytes(target["signedUrl"], data)
    ok(f"uploaded a 2-photo Ring-2 batch: lead={lead_id} tail={tail_id}")

    wall = check_wall(args.event_id, lead_id, uploaded_at, args.timeout)
    check_playlist_shape(args.event_id, wall)

    print("\n── the wall follows the event, not just the photos")
    check_stage_retheme(args.event_id, event)

    print("\n── the levers: the host fallback, the fingerprint guard, the tick lease")
    host_token = mint_host_token(args.event_id, api_key)
    check_host_tick(api, host_token, args.event_id)
    check_fingerprint_guard(args.event_id, uploaded_at)
    check_tick_lease(args.event_id)

    print()
    print(f"PASS  {args.event_id}: scheduler → tick → publisher → kiosk/playlist, unprompted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
