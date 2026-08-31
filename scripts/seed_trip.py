"""Seed the generic-timeline demo: a 5-day Japan group trip, through the real pipeline.

    python scripts/seed_trip.py
    python scripts/seed_trip.py --regen-cast     # fresh Nano Banana portraits for the 4 friends
    python scripts/seed_trip.py --regen-scenes   # fresh place establishing shots (~$0.40)

Sibling of `backend/seed.py` (the wedding demo) — same discipline, different shape. Where the
wedding seed proves the pyramid/VIP/ritual-glossary path, this one proves the generic
timeline-first product: multi-day stages with absolute UTC windows, `expectedParticipants`-driven
group coverage, and a flat VIP topology. It reuses `backend/seed.py::reset_event` verbatim (the
`hashes` register lesson documented there applies here exactly as it does to the wedding event) and
uploads every fixture through the same `POST /uploads` -> signed PUT -> Eventarc -> Curator/Face/
Guardian path a real guest's phone would use — never a direct Firestore write (spec 09 §5).

The stable event id is `japan_trip_2026`. Re-running wipes and reseeds it exactly like
`backend/seed.py --reset-only` does; the wedding demo events (`dev_demo`, `global_demo`) are never
touched by this script.

Deliberate story beat: today (Day 4) has no photo anywhere with 3 or more people in it. Every
fixture this script uploads is a solo portrait, an **empty** establishing shot of a place, or a
content-free synthetic image — the coverage gap the Story Director's next tick should notice and act
on is real, not staged into the document store.

The establishing shots (`_SCENES`) were added 2026-08-31 to fix a real defect in this demo rather
than to decorate it: with only portraits and gradients to look at, the Curator had no visual evidence
for any place-named stage, so `fusion.fuse` returned its honest null `stageId` and *every* photograph
landed in the `_unstaged` coverage shard. The per-stage coverage table read empty while the pipeline
was working correctly. See `_SCENES` for the two constraints those images have to respect — the first
of which is not breaking the group-coverage gap above.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1] / "backend"
EVAL = Path(__file__).resolve().parents[1] / "eval"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(SCRIPTS))

from PIL import Image  # noqa: E402

import cast as cast_module  # noqa: E402
import seed as wedding_seed  # noqa: E402  (backend/seed.py — reused, never edited for behavior)
from schemas.event import (  # noqa: E402
    DemoConfig,
    Event,
    EventClass,
    EventStage,
    EventStatus,
    EventTypeProfile,
    RequiredMoment,
    SensitivityProfile,
    VipTopology,
)
from schemas.person import Tier  # noqa: E402
from shared import coverage, fs, internal as face_internal  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

from smoke_safety import wait_for_indexed  # noqa: E402
from smoke_upload import put_bytes, register_intent, sign_in_anonymously  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARTIFACTS = EVAL / "artifacts"
CAST_DIR = ARTIFACTS / "cast_trip"
SCENE_DIR = ARTIFACTS / "scenes_trip"
RUN_FILE = ARTIFACTS / "seed_trip_run.json"

EVENT_ID = "japan_trip_2026"
TIMEZONE = "Asia/Tokyo"


def log(message: str) -> None:
    print(f"      {message}")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


# ---------------------------------------------------------------- the 4 trip friends


#: (slug, display name, prompt). All four enroll at Tier.INNER_CIRCLE (flat topology's default,
#: spec 11 §3) — a group of friends has no pyramid to sit inside. Casual travel dress, distinct
#: appearances, one consistent daylight-travel-photo lighting style so the four read as one trip's
#: cast, same reasoning `eval/cast.py`'s module docstring gives for its evening-lit wedding set.
#: Each background is deliberately a single deserted/empty setting with no other person anywhere
#: in frame — not "a blurred crowd", which the Curator's honest people-count estimate would (and
#: on the first draft of these prompts, did) read as a 4-15-person group shot, quietly closing the
#: very coverage gap this whole seed exists to leave open. Solo subject, empty background: the
#: `peopleCountEstimate` this is meant to produce is 1, full stop.
_MEMBERS = [
    (
        "arjun",
        "Arjun",
        "Photorealistic candid travel portrait of a fictional Indian man in his mid-twenties, "
        "short textured hair, wearing a denim jacket over a plain tee with a backpack strap "
        "visible on one shoulder, soft overcast daylight, standing alone with an empty quiet "
        "alleyway of blurred neutral walls behind him — no other people anywhere in the frame, "
        "shallow depth of field, 50mm lens, natural skin texture, easy warm smile, looking "
        "slightly off-camera. Photograph, not illustration. Vertical 3:4 framing, head and "
        "shoulders.",
    ),
    (
        "riya",
        "Riya",
        "Photorealistic candid travel portrait of a fictional Indian woman in her mid-twenties, "
        "long wavy hair loose in the wind, wearing a light knit cardigan over a striped top with "
        "small hoop earrings, soft overcast daylight, standing alone with an empty quiet alleyway "
        "of blurred neutral walls behind her — no other people anywhere in the frame, shallow "
        "depth of field, 50mm lens, natural skin texture, playful grin, looking slightly "
        "off-camera. Photograph, not illustration. Vertical 3:4 framing, head and shoulders.",
    ),
    (
        "kabir",
        "Kabir",
        "Photorealistic candid travel portrait of a fictional Indian man in his late twenties, "
        "round wire-frame glasses, a dark beanie, wearing a zip-up hoodie with a daypack strap "
        "visible on one shoulder, soft overcast daylight, standing alone with an empty quiet "
        "alleyway of blurred neutral walls behind him — no other people anywhere in the frame, "
        "shallow depth of field, 50mm lens, natural skin texture, relaxed half-smile, looking "
        "slightly off-camera. Photograph, not illustration. Vertical 3:4 framing, head and "
        "shoulders.",
    ),
    (
        "neha",
        "Neha",
        "Photorealistic candid travel portrait of a fictional Indian woman in her late twenties, "
        "hair pulled back in a ponytail, light freckles, wearing a packable windbreaker over a "
        "plain top, soft overcast daylight, standing alone with an empty quiet alleyway of "
        "blurred neutral walls behind her — no other people anywhere in the frame, shallow depth "
        "of field, 50mm lens, natural skin texture, caught mid-laugh, looking slightly "
        "off-camera. Photograph, not illustration. Vertical 3:4 framing, head and shoulders.",
    ),
]


def ensure_trip_cast(*, regenerate: bool = False) -> list[cast_module.CastMember]:
    """Generate (or reuse cached) portraits for the 4 trip friends.

    Same call, same retry/backoff, same client as `eval/cast.py::ensure_cast` — reused through the
    `generate_portrait` alias that module exports for exactly this — cached under its own
    `eval/artifacts/cast_trip/` directory so this never collides with (or invalidates) the wedding
    cast's cache, and a second `python scripts/seed_trip.py` costs nothing.
    """
    from google import genai

    CAST_DIR.mkdir(parents=True, exist_ok=True)
    client = genai.Client(enterprise=True, project=settings().project, location="global")

    members = []
    generated_this_run = False
    for slug, name, prompt in _MEMBERS:
        path = CAST_DIR / f"{slug}.jpg"
        if path.exists() and not regenerate:
            print(f"      cast: {slug} (cached)")
        else:
            if generated_this_run:
                time.sleep(8)  # spread calls out — the image model's quota is tight
            data = cast_module.generate_portrait(client, prompt)
            path.write_bytes(data)
            generated_this_run = True
            print(f"      cast: {slug} generated ({len(data) / 1024:.0f} KB, ~$0.045)")
        members.append(cast_module.CastMember(slug=slug, displayName=name, tier="inner_circle", photo=path))
    return members


def enroll_trip_cast(event_id: str, members: list[cast_module.CastMember]) -> list[dict[str, Any]]:
    """Host-declared enrollment at Tier.INNER_CIRCLE for all 4 — flat topology's default (spec 11
    §3): a group of friends has no principal to promote anyone above. Same embed call, same
    pre-embed downscale (`seed.selfie_b64`) `backend/seed.py::enroll_cast` uses for its VIPs."""
    enrolled = []
    for member in members:
        try:
            body = face_internal.embed_selfie(wedding_seed.selfie_b64(member.photo), max_faces=1, timeout_s=90.0)
        except face_internal.FaceServiceError as exc:
            log(f"WARN  embedding failed for {member.slug}: {exc}")
            continue
        faces = body.get("faces") or []
        if not faces:
            log(f"WARN  no face detected in {member.slug}'s portrait")
            continue
        person_id = wedding_seed.seed_person(
            event_id, faces[0]["embedding"], member.displayName, Tier.INNER_CIRCLE, host_enrolled=True
        )
        enrolled.append({"personId": person_id, "slug": member.slug, "displayName": member.displayName})
        log(f"cast enrolled: {member.displayName} (inner_circle) -> {person_id}")
    return enrolled


# ---------------------------------------------------------------- the event graph


#: (stageId, label, dayOffset relative to "today" = Day 4, startHour, startMin, endHour, endMin,
#: theme, requiredMoments). Absolute Asia/Tokyo local windows, converted to UTC when the `EventStage`
#: list is built — the multi-day contract spec 13 actually exercises, as opposed to the wedding
#: seed's hours-from-now windows.
_STAGE_PLAN: list[tuple[str, str, int, int, int, int, int, str, list[RequiredMoment]]] = [
    ("arrival", "Arrival", -3, 15, 0, 17, 0, "slate", []),
    (
        "shibuya_evening", "Shibuya Evening", -3, 18, 0, 21, 0, "neon",
        [RequiredMoment(momentId="crossing_shot", label="Crossing shot")],
    ),
    (
        "asakusa_morning", "Asakusa Morning", -2, 9, 0, 12, 0, "sunrise",
        [RequiredMoment(momentId="temple_gate", label="Temple gate")],
    ),
    ("akihabara", "Akihabara", -2, 13, 0, 17, 0, "violet", []),
    (
        "fushimi_inari", "Fushimi Inari", -1, 10, 0, 13, 0, "forest",
        [RequiredMoment(momentId="torii_gates", label="Torii gates")],
    ),
    ("gion_evening", "Gion Evening", -1, 18, 0, 21, 0, "crimson", []),
    ("kawaguchi_lake", "Kawaguchi Lake", 0, 9, 0, 12, 0, "gold", []),
    (
        "fuji_viewpoint", "Fuji Viewpoint", 0, 15, 0, 17, 0, "ocean",
        [RequiredMoment(momentId="establishing_shot", label="Establishing shot", tierWeight=1.5)],
    ),
    ("group_dinner", "Group Dinner", 0, 19, 0, 21, 0, "crimson", []),
    ("departure", "Departure", 1, 10, 0, 16, 0, "slate", []),
]


def _stage_window_local(today: dt.date, tz: ZoneInfo, plan_row: tuple) -> tuple[dt.datetime, dt.datetime]:
    _, _, day_offset, h1, m1, h2, m2, _, _ = plan_row
    day = today + dt.timedelta(days=day_offset)
    start = dt.datetime(day.year, day.month, day.day, h1, m1, tzinfo=tz)
    end = dt.datetime(day.year, day.month, day.day, h2, m2, tzinfo=tz)
    return start, end


def build_trip_stages(today: dt.date, tz: ZoneInfo) -> tuple[list[EventStage], dict[str, tuple[dt.datetime, dt.datetime]]]:
    """The 10 stages as `EventStage`s (UTC windows) plus a stageId -> (startLocal, endLocal) map
    fixtures use to pick an in-window `capturedAt` without re-deriving the same math twice."""
    stages: list[EventStage] = []
    local_windows: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    for row in _STAGE_PLAN:
        stage_id, label, _day_offset, _h1, _m1, _h2, _m2, theme, required = row
        start_local, end_local = _stage_window_local(today, tz, row)
        local_windows[stage_id] = (start_local, end_local)
        stages.append(
            EventStage(
                stageId=stage_id,
                label=label,
                startsAt=start_local.astimezone(dt.timezone.utc),
                endsAt=end_local.astimezone(dt.timezone.utc),
                theme=theme,
                requiredMoments=required,
            )
        )
    return stages, local_windows


def ensure_trip_event(event_id: str, today: dt.date, tz: ZoneInfo) -> tuple[dict[str, Any], dict[str, tuple[dt.datetime, dt.datetime]]]:
    """The Japan trip's Event doc: `startsOn`/`endsOn` position "today" as Day 4 of 5, custom
    template, flat topology, neutral sensitivity dials, no cultural glossary (spec 13's generic,
    non-wedding shape). `activeStage` is deliberately left unset — with 10 stages spread over 5
    days, pinning one stage id the way the wedding seed pins `sangeet` would freeze the schedule
    resolver (`shared/stages.py::resolve_active`) on whichever stage happened to be "now" at seed
    time; leaving it `None` lets the ordinary `stageOverride || activeStage || schedule` precedence
    fall through to the schedule on every read, which is the only answer that stays correct as the
    real clock moves through the 5-day window. Flagged here as a call the brief left open.
    """
    now = dt.datetime.now(dt.timezone.utc)
    starts_on = (today - dt.timedelta(days=3)).isoformat()
    ends_on = (today + dt.timedelta(days=1)).isoformat()
    stages, local_windows = build_trip_stages(today, tz)

    event = Event(
        eventId=event_id,
        name="Japan 2026 — Tokyo + Kyoto",
        timezone=TIMEZONE,
        status=EventStatus.LIVE,
        **{"class": EventClass.INTERNAL_DEV},
        startsOn=starts_on,
        endsOn=ends_on,
        expectedParticipants=4,
        stages=stages,
        activeStage=None,
        eventTypeProfile=EventTypeProfile(
            vipTopology=VipTopology.FLAT,
            sensitivityProfile=SensitivityProfile(),  # neutral defaults: context_dependent / standard
            culturalGlossary=[],
        ),
        demoConfig=DemoConfig(enabled=False),
        createdAt=now,
        liveAt=now,
    )
    payload = fs.to_firestore(event.model_dump(by_alias=True))
    fs.event_ref(event_id).set(payload, merge=True)
    return fs.get_event(event_id) or {}, local_windows


# ---------------------------------------------------------------- fixtures


def _gradient_jpeg(seed_value: int) -> bytes:
    """A visually near-empty synthetic image — the aesthetic floor's honest low score, same shape
    as `eval/fixtures.py::_gradient_jpeg` but written locally so this script depends on nothing in
    that module (the hard fence leaves its manifest untouched)."""
    rng = random.Random(seed_value)
    img = Image.new("RGB", (1200, 900))
    base = (rng.randint(30, 220), rng.randint(30, 220), rng.randint(30, 220))
    pixels = img.load()
    for y in range(0, 900, 3):
        for x in range(0, 1200, 3):
            shade = ((base[0] + x // 8) % 256, (base[1] + y // 8) % 256, base[2])
            for dy in range(3):
                for dx in range(3):
                    pixels[x + dx, y + dy] = shade
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


#: One place-specific establishing shot per stage that has a distinctive look. **The reason this
#: exists is a real finding, not polish.** Before it, every fixture this script uploaded was a solo
#: studio-lit portrait or a synthetic gradient — and the Curator, shown a head-and-shoulders portrait
#: and asked which of "Arrival / Shibuya Evening / Asakusa Morning / … / Departure" it looks like,
#: correctly answers *none of them*. The visual distribution comes back all zeros, `fusion.fuse`
#: returns a null `stageId` (its honest "no evidence" answer rather than crowning whichever stage the
#: clock favours), and every one of those photographs lands in the `_unstaged` coverage shard. The
#: result was a demo whose per-stage coverage table was empty while the pipeline was working
#: perfectly: the schedule knew when each photo was taken and the Curator had nothing to say about
#: where.
#:
#: **Two constraints, and the first one is the whole demo.** The Story Director's headline beat is
#: that *today* (Day 4) has no frame holding the group, so the group-coverage gap is genuinely open
#: (`ledger._group_gap`, threshold `ceil(4 × 0.75) = 3` people). A crowd scene would satisfy it and
#: quietly delete the story. So: every prompt demands **no people in frame**, and today's three
#: stages get only unambiguously empty landscapes — there is deliberately no `group_dinner` scene,
#: because a photograph of a dinner table is exactly the frame that would have people around it.
#: Second constraint: these are establishing shots of *places*, which is also what makes them fair
#: evidence — the Curator is being given something a guest really would photograph, not a hint.
#:
#: Cached in `eval/artifacts/scenes_trip/` after the first run (~$0.045 each through the same Nano
#: Banana call the cast uses, so ~$0.40 once, then free forever).
_SCENES: list[tuple[str, str]] = [
    (
        "arrival",
        "Narita airport arrivals hall, wide shot, polished floors, departure boards, soft daylight "
        "through tall windows. Completely empty of people. Documentary travel photograph, 24mm, "
        "natural colour. No text overlays.",
    ),
    (
        "shibuya_evening",
        "Shibuya scramble crossing at night from above, wet asphalt reflecting neon signage, "
        "Japanese shopfront lights in pink and blue. Completely empty street, no people, no cars. "
        "Cinematic night photograph, 35mm, long exposure.",
    ),
    (
        "asakusa_morning",
        "Senso-ji temple's Kaminarimon gate in Asakusa, giant red paper lantern, vermilion timber, "
        "clear early-morning light. Completely empty of people. Travel photograph, 28mm, crisp "
        "detail, natural colour.",
    ),
    (
        "akihabara",
        "Akihabara electronics district street level, stacked vertical signage, arcade facades, "
        "overcast afternoon light. Completely empty of people and vehicles. Documentary photograph, "
        "35mm, natural colour.",
    ),
    (
        "fushimi_inari",
        "Fushimi Inari torii gate tunnel, hundreds of vermilion gates receding into forest shade, "
        "dappled green light on stone steps. Completely empty of people. Travel photograph, 35mm, "
        "shallow depth of field.",
    ),
    (
        "gion_evening",
        "Gion district narrow lane at dusk, wooden machiya townhouses, warm paper lantern glow, "
        "damp stone paving. Completely empty of people. Cinematic photograph, 50mm, moody low light.",
    ),
    # --- today (Day 4). Landscapes only, and no dinner scene: see the header note.
    (
        "kawaguchi_lake",
        "Lake Kawaguchi in the morning, still water, Mount Fuji reflected, pine trees along the "
        "shoreline, pale blue sky. Completely empty of people and boats. Landscape photograph, "
        "24mm, natural colour.",
    ),
    (
        "fuji_viewpoint",
        "Mount Fuji from a high viewpoint in late afternoon, snow-capped summit above layered "
        "ridgelines, warm golden light, thin cloud. Completely empty of people. Landscape "
        "photograph, 70mm, crisp detail.",
    ),
]


def ensure_trip_scenes(*, regenerate: bool = False) -> dict[str, Path]:
    """Generate (or reuse cached) one establishing shot per `_SCENES` stage. See `_SCENES` for why.

    Degrades rather than fails: a scene that cannot be generated (quota, a refusal) is logged and
    skipped, and the seed proceeds with whichever ones exist. The demo is worse without them, but a
    missing landscape must not be the reason the whole trip event fails to seed.
    """
    from google import genai

    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    client = None
    generated_this_run = False

    for stage_id, prompt in _SCENES:
        path = SCENE_DIR / f"scene_{stage_id}.jpg"
        if path.exists() and not regenerate:
            log(f"scene: {stage_id} (cached)")
            paths[stage_id] = path
            continue
        if client is None:
            client = genai.Client(enterprise=True, project=settings().project, location="global")
        if generated_this_run:
            time.sleep(8)  # the image model's quota is tighter than text — see cast.py::_generate
        try:
            path.write_bytes(cast_module.generate_portrait(client, prompt))
        except Exception as exc:  # noqa: BLE001 - one missing landscape must not fail the seed
            log(f"WARN  scene {stage_id} could not be generated ({exc}) — skipping")
            continue
        generated_this_run = True
        log(f"scene: {stage_id} (generated)")
        paths[stage_id] = path
    return paths


def _at(local_windows: dict[str, tuple[dt.datetime, dt.datetime]], stage_id: str, minutes_in: int) -> dt.datetime:
    start, _end = local_windows[stage_id]
    return start + dt.timedelta(minutes=minutes_in)


def build_fixture_plan(
    members: list[cast_module.CastMember],
    local_windows: dict[str, tuple[dt.datetime, dt.datetime]],
    scenes: dict[str, Path] | None = None,
) -> list[dict[str, Any]]:
    """~22 uploads: 8 cast portraits across two past-day stages each, 8 place-specific establishing
    shots (one per `_SCENES` stage), 2 synthetic gradients, and 2 more portraits into today's
    (Day 4) `kawaguchi_lake` window.

    **Every entry is a single face or no face at all.** Deliberately no photo here ever holds 3+
    people, so today's group-coverage gap the Story Director should act on is real rather than
    staged into a document that would otherwise satisfy it. The scene shots are the load-bearing
    addition and they respect that same rule by construction — every prompt demands an empty frame,
    and there is no dinner-table scene (`_SCENES`).

    The gradients drop from 4 to 2 rather than to 0: their job is to demonstrate the aesthetic floor
    doing something honest (a content-free image scoring ~0.0 and staying off every public surface),
    and two of those is enough to show it without half the event's coverage being noise.
    """
    by_slug = {m.slug: m for m in members}
    scenes = scenes or {}
    plan: list[dict[str, Any]] = []

    # 8: each friend, twice, on two different Days 1-3 stages.
    pairs = [
        ("arjun", "arrival", 45, "public"),
        ("arjun", "fushimi_inari", 60, "pool"),
        ("riya", "shibuya_evening", 60, "pool"),
        ("riya", "gion_evening", 90, "public"),
        ("kabir", "asakusa_morning", 60, "public"),
        ("kabir", "fushimi_inari", 120, "pool"),
        ("neha", "akihabara", 90, "pool"),
        ("neha", "gion_evening", 120, "public"),
    ]
    for slug, stage_id, minutes_in, consent in pairs:
        member = by_slug[slug]
        plan.append(
            {
                "fixtureId": f"cast_{slug}_{stage_id}",
                "kind": "cast",
                "stageId": stage_id,
                "capturedLocal": _at(local_windows, stage_id, minutes_in),
                "consent": consent,
                "imageBytes": member.photo.read_bytes,
            }
        )

    # 8: the place-specific establishing shots — one per stage that has a look (`_SCENES`). These are
    # what give the Curator something to *see*, so each stage's coverage shard fills with its own
    # photographs instead of everything piling into `_unstaged`. Public ring: an empty landscape has
    # no subject to consent on behalf of, and a wall of the places you went is the point of a kiosk.
    # Placed 20 minutes into each window — a guest photographs where they are shortly after arriving.
    for stage_id, path in sorted(scenes.items()):
        if stage_id not in local_windows:
            continue
        plan.append(
            {
                "fixtureId": f"scene_{stage_id}",
                "kind": "scene",
                "stageId": stage_id,
                "capturedLocal": _at(local_windows, stage_id, 20),
                "consent": "public",
                "imageBytes": (lambda p=path: p.read_bytes()),
            }
        )

    # 2: content-free synthetics, to keep the aesthetic floor visibly doing its job.
    synthetics = [
        ("akihabara", 150),
        ("gion_evening", 150),
    ]
    for i, (stage_id, minutes_in) in enumerate(synthetics):
        seed_value = hash((stage_id, i)) & 0xFFFF
        plan.append(
            {
                "fixtureId": f"gradient_{stage_id}_{i}",
                "kind": "synthetic",
                "stageId": stage_id,
                "capturedLocal": _at(local_windows, stage_id, minutes_in),
                "consent": "pool",
                "imageBytes": lambda s=seed_value: _gradient_jpeg(s),
            }
        )

    # 2 more: today's `kawaguchi_lake` window, same portraits re-tagged — the precedent for reusing
    # a cast portrait under a second capture time is `eval/fixtures.py`'s `cast_{slug}_ceremony`.
    today_portraits = [("arjun", 60), ("riya", 90)]
    for slug, minutes_in in today_portraits:
        member = by_slug[slug]
        plan.append(
            {
                "fixtureId": f"cast_{slug}_kawaguchi_lake_today",
                "kind": "cast",
                "stageId": "kawaguchi_lake",
                "capturedLocal": _at(local_windows, "kawaguchi_lake", minutes_in),
                "consent": "public",
                "imageBytes": member.photo.read_bytes,
            }
        )

    return plan


def upload_fixture(
    api: str, api_key: str, event_id: str, item: dict[str, Any], timeout: float
) -> dict[str, Any]:
    raw = item["imageBytes"]()
    data = wedding_seed.stamp_captured_at(raw, item["capturedLocal"])

    token, _uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    target = register_intent(api, event_id, token, media_id, data, item["consent"])
    put_bytes(target["signedUrl"], data)
    log(
        f"{item['fixtureId']}: uploaded ({len(data)} bytes, stage={item['stageId']}, "
        f"capturedLocal={item['capturedLocal'].isoformat()})"
    )

    doc = wait_for_indexed(event_id, media_id, timeout)
    return {"fixtureId": item["fixtureId"], "mediaId": media_id, "stageId": item["stageId"], "status": doc.get("status")}


# ---------------------------------------------------------------- coverage table


_STAGE_LABELS = {row[0]: row[1] for row in _STAGE_PLAN}
_STAGE_ORDER = [row[0] for row in _STAGE_PLAN]


def print_coverage_table(event_id: str) -> None:
    shards = coverage.read(event_id)
    print()
    print(f"{'stage':<18}{'label':<20}{'photoCount':>11}{'publicCount':>13}{'highlightCount':>16}")
    for stage_id in _STAGE_ORDER + [sid for sid in shards if sid not in _STAGE_ORDER]:
        shard = shards.get(stage_id)
        if shard is None and stage_id not in shards:
            print(f"{stage_id:<18}{_STAGE_LABELS.get(stage_id, ''):<20}{0:>11}{0:>13}{0:>16}")
            continue
        print(
            f"{stage_id:<18}{_STAGE_LABELS.get(stage_id, ''):<20}"
            f"{shard.photo_count:>11}{shard.public_count:>13}{shard.highlight_count:>16}"
        )


# ---------------------------------------------------------------- main


def main() -> int:
    cfg = settings()
    cfg.require("project")

    ap = argparse.ArgumentParser(description="Seed the Japan-trip demo event through the real pipeline.")
    ap.add_argument("--event-id", default=EVENT_ID)
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--timeout", type=float, default=150.0)
    ap.add_argument("--regen-cast", action="store_true")
    ap.add_argument(
        "--regen-scenes",
        action="store_true",
        help="force fresh place establishing shots (~$0.40; otherwise cached in eval/artifacts)",
    )
    ap.add_argument("--no-reset", action="store_true", help="keep whatever is already on the event")
    ap.add_argument("--reset-only", action="store_true", help="wipe the event's people/media/ops and exit")
    args = ap.parse_args()

    event_id = args.event_id

    if args.reset_only:
        wedding_seed.reset_event(event_id)
        print(f"PASS  reset {event_id}")
        return 0

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")

    tz = ZoneInfo(TIMEZONE)
    today = dt.datetime.now(tz).date()

    if not args.no_reset:
        wedding_seed.reset_event(event_id)

    event, local_windows = ensure_trip_event(event_id, today, tz)
    log(
        f"event {event_id} ready (status={event.get('status')}, class={event.get('class')}, "
        f"startsOn={event.get('startsOn')}, endsOn={event.get('endsOn')}, today=Day 4)"
    )

    log("generating/loading the 4 trip friends (Nano Banana) —")
    members = ensure_trip_cast(regenerate=args.regen_cast)

    log("enrolling the 4 friends as host-declared, tier inner_circle (flat topology) people —")
    cast_records = enroll_trip_cast(event_id, members)

    log("generating/loading one empty establishing shot per place (Nano Banana) —")
    scenes = ensure_trip_scenes(regenerate=args.regen_scenes)

    fixture_plan = build_fixture_plan(members, local_windows, scenes)
    log(f"uploading {len(fixture_plan)} fixtures through the real pipeline —")
    items = []
    for item in fixture_plan:
        try:
            items.append(upload_fixture(api, api_key, event_id, item, args.timeout))
        except SystemExit:
            raise
        except Exception as exc:  # a stuck fixture should not abort the whole seed run
            log(f"WARN  {item['fixtureId']} failed to seed: {exc}")
            items.append({"fixtureId": item["fixtureId"], "mediaId": None, "error": str(exc)})

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    RUN_FILE.write_text(
        json.dumps(
            {
                "eventId": event_id,
                "seededAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "cast": cast_records,
                "items": items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    ok_count = sum(1 for i in items if i.get("mediaId"))
    print_coverage_table(event_id)

    print()
    print(f"PASS  seeded {ok_count}/{len(items)} fixtures into {event_id} -> {RUN_FILE}")
    print(f"      cast personIds: {', '.join(c['personId'] for c in cast_records)}")
    print(
        "      group gap is live for today — the next director tick may issue a targeted "
        "group_shot bounty"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
