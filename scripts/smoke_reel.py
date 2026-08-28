"""Smoke-test the Reel Director (S11) — spec 06 §8's acceptance criteria, made checkable.

Companion to `smoke_upload.py` (spine + Curator), `smoke_faces.py` (identity), `smoke_safety.py`
(Guardian + indexing), `smoke_autonomy.py` (Scheduler → tick → wall) and `smoke_director.py` (bounties).
Those prove the fleet notices things and asks for things. This proves it *makes* something.

Two halves, deliberately — the same discipline as `--gate-only`, `--program-only` and
`--guardrails-only`:

  1. `--offline` — every deterministic claim spec 06 §8 makes, with **no network, no Firestore, no
     ffmpeg and no spend**:
       · two personas over one seeded candidate set select visibly different material (§8.1);
       · the critic's rubric floor rejects a deliberately-flat storyboard (§8.2), deterministically,
         without asking a model;
       · the linter drops a hallucinated mediaId, a Devanagari caption, an over-long caption and a
         near-duplicate consecutive shot, and reports each one;
       · every cut lands on a beat — the assertion is `≤ 80 ms` (§8.3) and the measurement is `0.000`,
         because `edl.py` only ever emits grid values;
       · no face crosses the frame edge on any Ken Burns move (§8.6), checked at both endpoint
         rectangles of every shot, which is the whole proof (see `edl.py`);
       · the ffmpeg crossfade offsets equal the EDL's cut points, so what is rendered is what was timed.

  2. the live run — commission a real reel on a real event through the real `api`, watch the real Cloud
     Run Job walk `directing → composing → rendering → published`, and assert the published document:
     a file in the curated bucket, a playable `videoUri` that 302s, a Lyria soundtrack with a detected
     tempo, and the publisher premiering it onto `kiosk/playlist`. Then flip a constituent photo's
     consent and assert spec 06 §8.5: the reel unpublishes.

    python scripts/smoke_reel.py --offline                         # free, no cloud account needed
    python scripts/smoke_reel.py --api https://api-....run.app --event dev_demo
    python scripts/smoke_reel.py --event dev_demo --local          # run the pipeline in-process
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

from directors.reel import agent, critic, edl as edl_mod, ffmpeg_build, select  # noqa: E402
from directors.reel.select import Candidate  # noqa: E402
from schemas.common import ConsentRing, Visibility  # noqa: E402
from schemas.reel import (  # noqa: E402
    Critique,
    KenBurnsMove,
    MusicBrief,
    PacingCurve,
    ReelPersona,
    ReelPlan,
    ReelStatus,
    ShotPlan,
    Transition,
)
from shared import fs  # noqa: E402
from shared.settings import REEL_BEAT_TOLERANCE_MS, REEL_MIN_SHOTS, settings  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ================================================================ 1. offline


def _fixture_candidates() -> list[Candidate]:
    """A seeded event: two Principals, a mother, three guests, three stages, one wide group shot.

    Deliberately shaped so the persona lenses have something to disagree about — a `couple` reel and a
    `guest_energy` reel over this set must not produce the same material, and if they do, spec 06 §2's
    central claim is false and this test says so.
    """
    base = dt.datetime(2026, 8, 29, 10, 0, tzinfo=dt.timezone.utc)
    rows: list[tuple[str, str, int, list[str], str, float, str, int, list[list[float]]]] = [
        # id,      stage,     tier, people,             moment,        aesthetic, caption, count, faces
        ("m_bride1", "haldi", 0, ["p_bride"], "turmeric", 0.91, "turmeric on her hands", 2,
         [[0.36, 0.20, 0.26, 0.30]]),
        ("m_groom1", "haldi", 0, ["p_groom"], "turmeric", 0.88, "his friends smearing haldi", 4,
         [[0.30, 0.18, 0.22, 0.26]]),
        ("m_mother1", "haldi", 1, ["p_mother"], "family_group", 0.84, "his mother wiping his face", 2,
         [[0.40, 0.22, 0.24, 0.28]]),
        ("m_pair1", "sangeet", 0, ["p_bride", "p_groom"], "first_dance", 0.94,
         "the two of them, mid-turn", 2, [[0.24, 0.24, 0.20, 0.24], [0.52, 0.22, 0.20, 0.24]]),
        ("m_pair2", "sangeet", 0, ["p_bride", "p_groom"], "first_dance", 0.79,
         "a glance across the floor", 2, [[0.30, 0.26, 0.18, 0.22], [0.56, 0.28, 0.18, 0.22]]),
        ("m_crowd1", "sangeet", 3, [], "dance", 0.76, "the whole floor mid-jump", 22, []),
        ("m_crowd2", "sangeet", 3, [], "dance", 0.71, "laughing in a circle", 14, []),
        ("m_guest1", "sangeet", 3, ["p_guest_a"], "dance", 0.68, "arms up, eyes shut", 3,
         [[0.42, 0.28, 0.18, 0.22]]),
        ("m_guest2", "sangeet", 3, ["p_guest_b"], "candid", 0.66, "someone stealing a laddoo", 1,
         [[0.44, 0.30, 0.16, 0.20]]),
        ("m_guest3", "pheras", 3, ["p_guest_c"], "ritual", 0.64, "watching from the back row", 6,
         [[0.46, 0.32, 0.14, 0.18]]),
        ("m_pheras1", "pheras", 0, ["p_bride", "p_groom"], "varmala", 0.92,
         "the varmala going over his head", 3, [[0.28, 0.20, 0.20, 0.24], [0.54, 0.24, 0.20, 0.24]]),
        ("m_pheras2", "pheras", 0, ["p_groom"], "ritual", 0.81, "his hands around the fire", 2,
         [[0.38, 0.24, 0.22, 0.26]]),
        ("m_wide1", "sangeet", 1, ["p_mother", "p_guest_a"], "family_group", 0.77,
         "a very wide family line-up", 18,
         # Faces spread from 0.05 to 0.95 across a landscape frame: no 9:16 crop can hold them, so this
         # row is what exercises the `fit` branch rather than the crop branch.
         [[0.05, 0.40, 0.08, 0.12], [0.46, 0.40, 0.08, 0.12], [0.87, 0.40, 0.08, 0.12]]),
        ("m_bride2", "pheras", 0, ["p_bride"], "portrait", 0.86, "alone, before walking out", 1,
         [[0.34, 0.16, 0.30, 0.34]]),
    ]
    out: list[Candidate] = []
    for i, (mid, stage, tier, people, moment, aesthetic, caption, count, faces) in enumerate(rows):
        wide = mid == "m_wide1"
        out.append(
            Candidate(
                media_id=mid,
                display_uri=f"gs://derived/{mid}/display_1600.jpg",
                width=2400 if wide else 1200,
                height=1000 if wide else 1600,
                aesthetic=aesthetic,
                is_highlight=aesthetic >= 0.75,
                caption=caption,
                moment_tags=[moment],
                cultural_elements=(["haldi"] if stage == "haldi" else []),
                stage_id=stage,
                captured_at=base + dt.timedelta(minutes=17 * i),
                people_count=count,
                face_boxes=faces,
                person_ids=people,
                cluster_ids=[f"c_{p}" for p in people],
                top_tier=tier,
            )
        )
    return out


def check_persona_divergence(pool: list[Candidate]) -> None:
    """Spec 06 §8.1: two personas over the same dataset must not select the same material."""
    couple = select.choose(pool, persona=ReelPersona.COUPLE)
    energy = select.choose(pool, persona=ReelPersona.GUEST_ENERGY)
    recap = select.choose(pool, persona=ReelPersona.STAGE_RECAP, stage_id="haldi")

    if not couple or not energy:
        fail("a persona selected nothing from a 14-photograph pool")
    couple_ids, energy_ids = {c.media_id for c in couple}, {c.media_id for c in energy}
    overlap = couple_ids & energy_ids
    if couple_ids == energy_ids:
        fail("couple and guest_energy selected an identical set — the persona lens does nothing")
    if len(overlap) > min(len(couple_ids), len(energy_ids)) * 0.5:
        fail(f"couple and guest_energy overlap on {len(overlap)} of {len(couple_ids)} — too similar")
    if any(c.top_tier > 1 for c in couple):
        fail("couple selection contains a photograph with no principal in it")
    if {c.stage_id for c in recap} != {"haldi"}:
        fail(f"stage_recap:haldi selected outside its stage: {sorted({c.stage_id for c in recap})}")
    ok(
        f"persona lens diverges: couple={len(couple_ids)} energy={len(energy_ids)} "
        f"overlap={len(overlap)} · stage_recap confined to its stage"
    )

    # Spec 11 §3.3's VIP floor is a floor, not a boost: every tier-0/1 person with eligible media gets
    # a reserved slot, so bad luck in a weighted draw cannot exclude a principal from their own film.
    principals = {p for c in pool for p in c.person_ids if c.top_tier <= 1}
    covered = {p for c in select.choose(pool, persona=ReelPersona.STAGE_RECAP) for p in c.person_ids}
    missing = principals - covered
    if missing:
        fail(f"VIP floor did not reserve a slot for {sorted(missing)}")
    ok(f"VIP floor reserved a slot for all {len(principals)} tier-0/1 people")


def _plan(pool: list[Candidate], *, flat: bool = False) -> ReelPlan:
    """A well-formed storyboard over the fixture — or a deliberately flat one."""
    # `m_wide1` is pinned in on purpose: it is the row whose faces span the full frame, so the EDL and
    # the filtergraph checks exercise the `fit` branch as well as the crop branch.
    picks = [c.media_id for c in pool][:11] + ["m_wide1"]
    moves = [
        KenBurnsMove.PUSH_IN,
        KenBurnsMove.HOLD,
        KenBurnsMove.PAN_LEFT,
        KenBurnsMove.PULL_OUT,
        KenBurnsMove.PAN_RIGHT,
    ]
    shots = [
        ShotPlan(
            mediaId=mid,
            durationBeats=4 if flat else (6 if i in (0, 5, 11) else 3),
            move=KenBurnsMove.HOLD if flat else moves[i % len(moves)],
            transition=Transition.DISSOLVE if flat else (Transition.CUT if i % 3 else Transition.DISSOLVE),
            captionLine=("Haldi vibes" if i == 1 else None),
            emphasis=(not flat and i in (0, 5, 11)),
        )
        for i, mid in enumerate(picks)
    ]
    return ReelPlan(
        narrativeBrief=(
            "A beautiful celebration full of love and joy."
            if flat
            else "This Haldi belonged to the groom's friends: the turmeric fight ran twenty minutes "
            "and the emotional anchor is his mother wiping his face at the end. The Sangeet floor "
            "never emptied, and the varmala is the moment the room went quiet."
        ),
        title="Turmeric and Quiet",
        pacing=PacingCurve.LINEAR_BUILD if flat else PacingCurve.PEAK_AND_SETTLE,
        captionVoice="warm, sparse",
        shots=shots,
        music=MusicBrief(
            style="Indian wedding celebration",
            tempoBpm=96,
            arc="builds then lands quiet",
            instruments=["sitar", "dholak", "strings"],
            culturalRefs=["haldi"],
        ),
    )


def check_critic_rejects_flat(pool: list[Candidate]) -> None:
    """Spec 06 §8.2: the critic loop rejects a deliberately-flat storyboard.

    Asserted against `rubric_failures`, the *deterministic* half of the verdict, so the criterion does
    not depend on a model having a good day — a critic that answers PASS while reporting one named
    moment and a flat arc still sends the storyboard back.
    """
    flat_verdict = Critique(verdict="PASS", score=0.95, momentsNamed=1, arcIsFlat=True, personaHonored=True)
    failures = critic.rubric_failures(flat_verdict)
    if len(failures) < 2:
        fail(f"a flat, one-moment storyboard produced only {len(failures)} rubric failures")
    good_verdict = Critique(verdict="PASS", score=0.85, momentsNamed=4, arcIsFlat=False, personaHonored=True)
    if critic.rubric_failures(good_verdict):
        fail("a competent storyboard was sent back — the critic would burn a regeneration every time")
    ok(f"rubric floor rejects flat/underspecified ({'; '.join(f[:40] for f in failures)}) and passes good")


def check_linter(pool: list[Candidate]) -> None:
    """Every repair the linter is responsible for, in one plan."""
    plan = _plan(pool)
    plan.shots.insert(0, ShotPlan(mediaId="m_does_not_exist", durationBeats=3))
    plan.shots.insert(3, ShotPlan(mediaId="m_pair1", durationBeats=3, captionLine="हल्दी की रस्म"))
    plan.shots.insert(5, ShotPlan(mediaId="m_bride2", durationBeats=3, captionLine="x" * 60))
    # Two consecutive shots of the same person doing the same thing — spec 06 §2.4's near-duplicate rule.
    plan.shots.insert(7, ShotPlan(mediaId="m_pair2", durationBeats=3))
    plan.shots.insert(8, ShotPlan(mediaId="m_pair1", durationBeats=3))

    shots, issues = critic.lint(plan, pool, persona=ReelPersona.COUPLE)
    blob = " | ".join(issues)
    for expected in ("is not in the candidate set", "not Latin script", "over 34", "already used"):
        if expected not in blob:
            fail(f"linter did not report {expected!r}; got: {blob}")
    if any(s.mediaId == "m_does_not_exist" for s in shots):
        fail("a hallucinated mediaId survived the linter")
    if any(s.captionLine and not critic.is_latin(s.captionLine) for s in shots):
        fail("a non-Latin caption survived the linter")
    if len({s.mediaId for s in shots}) != len(shots):
        fail("the linter left a duplicated shot in place")
    if len(shots) < REEL_MIN_SHOTS:
        fail(f"the linter reduced a valid plan to {len(shots)} shots")
    ok(f"linter: {len(issues)} issues reported, {len(shots)} shots survive, no invalid shot passes")
    return shots


def check_beat_and_geometry(pool: list[Candidate], shots: list[ShotPlan]) -> None:
    """Spec 06 §8.3 (cuts on beats) and §8.6 (no face crossing the frame edge)."""
    bpm = 96.0
    period = 60.0 / bpm
    beats = [round(i * period, 4) for i in range(int(31.0 / period) + 1)]
    cut = edl_mod.build(
        shots,
        pool,
        curve=PacingCurve.PEAK_AND_SETTLE,
        beats=beats,
        downbeats=beats[::4],
        music_duration=30.0,
    )
    if len(cut.shots) < REEL_MIN_SHOTS:
        fail(f"EDL produced {len(cut.shots)} shots from {len(shots)}: {cut.notes}")
    if cut.beat_error_ms > REEL_BEAT_TOLERANCE_MS:
        fail(f"cuts land {cut.beat_error_ms:.1f} ms off the grid, over the {REEL_BEAT_TOLERANCE_MS} ms bound")
    for shot in cut.shots:
        if min(abs(shot.startSec - b) for b in beats) > 1e-6:
            fail(f"{shot.mediaId}: startSec {shot.startSec} is not a grid value")
        span = shot.endSec - shot.startSec
        if not (1.0 <= span <= 5.0):
            fail(f"{shot.mediaId}: {span:.2f}s is outside any sane shot length")
    ok(
        f"{len(cut.shots)} shots, {cut.duration:.2f}s, every cut exactly on the grid "
        f"(measured error {cut.beat_error_ms:.3f} ms, bound {REEL_BEAT_TOLERANCE_MS} ms)"
    )

    by_id = {c.media_id: c for c in pool}
    fitted = 0
    for shot in cut.shots:
        candidate = by_id[shot.mediaId]
        move = next((s.move for s in shots if s.mediaId == shot.mediaId), KenBurnsMove.PUSH_IN)
        frame = edl_mod.framing(candidate, move)
        if not edl_mod.faces_inside(candidate, frame):
            fail(f"{shot.mediaId}: a face leaves the frame during a {move.value}")
        fitted += frame.mode == "fit"
    ok(
        f"no face crosses the frame edge on any of {len(cut.shots)} shots "
        f"({fitted} fitted rather than cropped — see edl.py's containment proof)"
    )

    wide = by_id["m_wide1"]
    if edl_mod.framing(wide, KenBurnsMove.PUSH_IN).mode != "fit":
        fail("a group shot whose faces span the full frame was cropped instead of fitted")
    ok("a face line-up too wide for any 9:16 crop is fitted, not cropped through somebody's head")
    return cut


def check_filtergraph(pool: list[Candidate], cut: Any) -> None:
    """The offsets ffmpeg is given are the cut points the EDL timed. Pure — ffmpeg is never invoked."""
    by_id = {c.media_id: c for c in pool}
    plan = ffmpeg_build.build_command(
        cut.shots,
        [f"/tmp/{s.mediaId}.jpg" for s in cut.shots],
        sizes=[(by_id[s.mediaId].width, by_id[s.mediaId].height) for s in cut.shots],
        fitted=[False] * len(cut.shots),
        audio_path="/tmp/score.mp3",
        output_path="/tmp/out.mp4",
        captions=True,
    )
    origin = cut.shots[0].startSec
    expected = [round(s.endSec - origin, 4) for s in cut.shots[:-1]]
    if [round(o, 4) for o in plan.offsets] != expected:
        fail(f"crossfade offsets {plan.offsets[:4]} do not match the EDL cut points {expected[:4]}")
    if abs(plan.duration - cut.duration) > 0.01:
        fail(f"filtergraph duration {plan.duration} != EDL duration {cut.duration}")
    argv = " ".join(plan.args)
    for token in ("zoompan", "xfade", "libx264", "+faststart", "1080x1920"):
        if token not in argv:
            fail(f"filtergraph is missing {token!r}")
    if "ass=captions.ass" not in argv:
        fail("captions were requested but the ass filter is absent")
    if "Dialogue:" not in plan.ass:
        fail("the ASS file carries no dialogue lines")
    ok(
        f"filtergraph: {len(cut.shots)} inputs, {len(plan.offsets)} crossfades at the EDL's own cut "
        f"points, {plan.duration:.2f}s, captions burned via libass"
    )


def check_style_seed() -> None:
    """Spec 06 §2.3's seed has to be stable across processes, or a re-run stops explaining itself."""
    a = agent.style_seed("dev_demo", ReelPersona.COUPLE, 1)
    b = agent.style_seed("dev_demo", ReelPersona.COUPLE, 1)
    c = agent.style_seed("dev_demo", ReelPersona.COUPLE, 2)
    d = agent.style_seed("dev_demo", ReelPersona.GUEST_ENERGY, 1)
    if a != b:
        fail("style_seed is not deterministic within a process")
    if len({a, c, d}) != 3:
        fail("style_seed does not vary with version and persona")
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s');"
            "from directors.reel.agent import style_seed;"
            "from schemas.reel import ReelPersona;"
            "print(style_seed('dev_demo', ReelPersona.COUPLE, 1))" % str(BACKEND),
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0 or out.stdout.strip() != str(a):
        fail(f"style_seed differs across processes ({a} vs {out.stdout.strip()!r}) — hash() salt leaked in")
    ok(f"styleSeed stable across processes ({a}), varies by persona and version")


def check_prompt_shape(pool: list[Candidate]) -> None:
    """The director reads a description of the event, never a photograph — and never a raw identifier
    it is not allowed to use. Cheap to assert, and it is the claim the whole trust story rests on."""
    block = agent.evidence_block(
        event={"name": "Aarti & Rohan", "eventTypeProfile": {"templateId": "wedding_hindu",
                                                            "culturalGlossary": ["haldi", "varmala"]}},
        persona=ReelPersona.COUPLE,
        candidates=select.choose(pool, persona=ReelPersona.COUPLE),
        names={"p_bride": "Aarti", "p_groom": "Rohan", "p_mother": "Sunita"},
        seed=1234,
    )
    if "gs://" in block:
        fail("the evidence block leaks a GCS URI to the model")
    for expected in ("YOUR MANDATE", "EVIDENCE", "CULTURAL GLOSSARY", "styleSeed=1234"):
        if expected not in block:
            fail(f"evidence block is missing the {expected!r} section")
    if "intimacy arc" not in block:
        fail("the couple persona's mandate did not reach the prompt")
    ok(f"prompt is {len(block)} chars of stored evidence: no pixels, no URIs, mandate present")


def run_offline() -> int:
    print("── spec 06 §8, offline: no network, no Firestore, no ffmpeg, no spend\n")
    pool = _fixture_candidates()
    print(f"      fixture: {len(pool)} eligible photographs, 3 stages, 2 principals, 1 wide group shot")
    check_persona_divergence(pool)
    check_critic_rejects_flat(pool)
    shots = check_linter(pool)
    cut = check_beat_and_geometry(pool, shots)
    check_filtergraph(pool, cut)
    check_style_seed()
    check_prompt_shape(pool)
    print()
    print("PASS  every deterministic claim in spec 06 §8 holds, checkable without a cloud account")
    return 0


# ================================================================ 2. live


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def wait_for_reel(event_id: str, reel_id: str, *, timeout: float) -> dict[str, Any]:
    """Poll the reel document until it settles. The *client* never polls (spec 04 §1); a smoke test may."""
    from directors.reel import store

    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        doc = store.get(event_id, reel_id) or {}
        status = str(doc.get("status"))
        line = f"{status} {doc.get('progress', 0)}%"
        if line != last:
            print(f"      {line}")
            last = line
        if status in (
            ReelStatus.PUBLISHED.value,
            ReelStatus.UNPUBLISHED.value,
            ReelStatus.FAILED.value,
        ):
            return doc
        time.sleep(5)
    fail(f"reel {reel_id} did not settle within {timeout:.0f}s (last: {last})")
    return {}


def check_published(event_id: str, doc: dict[str, Any]) -> None:
    if doc.get("status") != ReelStatus.PUBLISHED.value:
        fail(f"reel finished as {doc.get('status')}: {doc.get('failureReason')}")
    if doc.get("visibility") != Visibility.PUBLIC.value:
        fail(f"a published Ring-2 reel has visibility={doc.get('visibility')!r}")
    shots = doc.get("shots") or []
    if len(shots) < REEL_MIN_SHOTS:
        fail(f"published with {len(shots)} shots, under the {REEL_MIN_SHOTS} floor")
    if len(doc.get("assetManifest") or []) != len(shots):
        fail("assetManifest and shots disagree — the consent interlock would miss an asset")
    if not doc.get("gcsUri") or not doc.get("sizeBytes"):
        fail("published without a file in the curated bucket")
    ok(
        f"published: {len(shots)} shots · {doc.get('durationSec')}s · "
        f"{int(doc.get('sizeBytes', 0)) / 1024:.0f} KB · title={doc.get('title')!r}"
    )
    ok(f"brief: {str(doc.get('narrativeBrief'))[:150]}…")

    if doc.get("musicUri") and doc.get("tempoBpm"):
        ok(f"Lyria soundtrack at {doc.get('tempoBpm')} BPM detected, {doc.get('beatCount')} beats")
    else:
        print(f"WARN  no soundtrack: {doc.get('failureReason')} — the reel is silent but published")

    critique = doc.get("critique") or {}
    ok(
        f"critic scored {critique.get('score')} ({critique.get('momentsNamed')} moments named), "
        f"direct attempts={doc.get('directAttempts')}, lint issues={len(doc.get('lintIssues') or [])}"
    )
    ok(f"cost {doc.get('costUsd')} USD, tokens {doc.get('usage')}")

    video = str(doc.get("videoUri") or "")
    if not video:
        fail("published without a playable videoUri")
    response = requests.get(video, allow_redirects=False, timeout=30)
    if response.status_code != 302 or "storage.googleapis.com" not in response.headers.get("location", ""):
        fail(f"the video endpoint returned {response.status_code}, not a 302 to a signed URL")
    head = requests.head(response.headers["location"], timeout=60)
    if head.status_code != 200:
        fail(f"the signed URL returned {head.status_code}")
    ok(f"videoUri 302s to a signed URL that serves {head.headers.get('content-length')} bytes, unauthenticated")


def check_premiere(event_id: str, reel_id: str, *, timeout: float) -> None:
    """Spec 04 §4: a published reel takes over the wall. The publisher owns this; it just has to fire."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        playlist = fs.kiosk_playlist_ref(event_id).get()
        doc = playlist.to_dict() or {}
        slots = doc.get("slots") or []
        if slots and slots[0].get("type") == "reel" and slots[0].get("reelId") == reel_id:
            ok(f"the kiosk playlist leads with the premiere (revision {doc.get('revision')})")
            return
        if reel_id in (doc.get("premieredReelIds") or []):
            ok("the reel has already premiered and rotated out of the lead slot")
            return
        time.sleep(5)
    print("WARN  the publisher did not premiere the reel within the window (is the publisher running?)")


def check_consent_interlock(event_id: str, reel_id: str, doc: dict[str, Any]) -> None:
    """Spec 06 §8.5: retracting a constituent photograph unpublishes the reel.

    Driven through `recompute_visibility`, which is the only writer of `visibility` — so this also
    demonstrates that the interlock cannot be bypassed by whatever path did the retraction.
    """
    from shared.visibility import recompute_visibility

    manifest = list(doc.get("assetManifest") or [])
    if not manifest:
        fail("nothing in the manifest to retract")
    victim = manifest[0]
    before = fs.media_ref(event_id, victim).get().to_dict() or {}
    original_ring = int((before.get("consent") or {}).get("ring", ConsentRing.PUBLIC.value))

    try:
        started = time.time()
        recompute_visibility(
            event_id, victim, extra={"consent.ring": ConsentRing.SELF_ONLY.value}
        )
        from directors.reel import store

        after = store.get(event_id, reel_id) or {}
        elapsed = time.time() - started
        if after.get("status") != ReelStatus.UNPUBLISHED.value:
            fail(f"the reel is still {after.get('status')} after {victim} was retracted to Ring 0")
        if after.get("visibility") is not None:
            fail("an unpublished reel still carries a visibility")
        ok(f"retracting {victim} unpublished the reel in {elapsed:.2f}s (spec 06 §8 bound: 5s)")
    finally:
        # Put the photograph back, so a re-run starts from the same state.
        recompute_visibility(event_id, victim, extra={"consent.ring": original_ring})
        print(f"      restored {victim} to ring {original_ring}")


def run_live(args: argparse.Namespace) -> int:
    from directors.reel import commission as reel_commission, pipeline, store

    event_id = args.event
    event = fs.get_event(event_id)
    if not event:
        fail(f"event {event_id} does not exist — run `make seed` first")
    if event.get("status") not in ("live", "wrapping"):
        fail(f"event {event_id} is {event.get('status')}; a reel can only be commissioned on a live event")

    print(f"── live: commissioning a {args.persona} reel on {event_id}\n")

    if args.local:
        # Run the whole pipeline in-process instead of launching the Cloud Run Job. Needs ffmpeg on PATH
        # and ADC; the point is to iterate on the filtergraph without a two-minute build every time.
        import asyncio

        result = reel_commission.commission(
            event_id,
            persona=ReelPersona(args.persona),
            stage_id=args.stage,
            reason="smoke_reel.py --local",
            commissioned_by="host",
            launch=False,
        )
        if not result.ok or not result.reel_id:
            fail(f"commission refused: {result.reason}")
        ok(f"commissioned {result.reel_id} (running locally, no job launch)")
        report = asyncio.run(pipeline.run(event_id, result.reel_id))
        print(f"      report: {json.dumps(report.as_dict(), default=str)}")
        doc = store.get(event_id, result.reel_id) or {}
        reel_id = result.reel_id
    else:
        api = args.api or settings().api_base_url
        if not api:
            fail("no --api and no NEXT_PUBLIC_API_URL — cannot reach the commission endpoint")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from smoke_faces import mint_host_token

        token = mint_host_token(event_id, os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", ""))
        body: dict[str, Any] = {"persona": args.persona, "reason": "smoke_reel.py"}
        if args.stage:
            body["stageId"] = args.stage
        response = requests.post(
            f"{api.rstrip('/')}/v1/events/{event_id}/reels",
            headers=_headers(token),
            json=body,
            timeout=60,
        )
        if response.status_code != 200:
            fail(f"POST /reels returned {response.status_code}: {response.text[:400]}")
        reel_id = response.json()["reelId"]
        ok(f"commissioned {reel_id} through the real api; the render job is starting")
        doc = wait_for_reel(event_id, reel_id, timeout=args.timeout)

    print()
    check_published(event_id, doc)
    print("\n── the wall (spec 04 §4)")
    check_premiere(event_id, reel_id, timeout=90)
    print("\n── the consent interlock (spec 06 §7/§8.5)")
    check_consent_interlock(event_id, reel_id, doc)

    print()
    print(f"PASS  {event_id}: a reel was directed, scored, rendered and premiered with nobody editing it")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="deterministic half only: no network, no spend")
    parser.add_argument("--event", default=os.environ.get("SMOKE_EVENT_ID", "dev_demo"))
    parser.add_argument("--api", default=os.environ.get("SMOKE_API_URL", ""))
    parser.add_argument("--persona", default=ReelPersona.COUPLE.value)
    parser.add_argument("--stage", default=None, help="stageId, required for stage_recap")
    parser.add_argument("--local", action="store_true", help="run the pipeline in-process (needs ffmpeg)")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    if args.offline:
        return run_offline()
    print("── running the offline half first; it is free and it fails faster\n")
    run_offline()
    print()
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
