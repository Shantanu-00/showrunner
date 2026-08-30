"""The ~25 golden fixtures and what the pipeline is expected to do with each of them.

This is the one file `seed.py` and `run_eval.py` both import, so the fixture a photo was seeded
as and the fixture `run_eval.py` grades it against can never drift apart. Every fixture is either
a cast portrait (real perceptual content, generated — never a real guest) or a synthetic image
built in-memory (a controlled negative: a gradient or solid fill has ~zero aesthetic evidence, so
stage attribution should fall through almost entirely to the temporal prior — spec 03 §5.1).

Expectations are deliberately loose. This is an eval report, not a unit test: a Gemini call is not
required to land on the same score twice, and CLAUDE.md's "never improvise thresholds" rule is
about product config (τ_match, the public floor), not about a report's grading bands. The
0.35 / 0.40 split here is this file's own choice, informed by the one empirical data point on
record (HANDOFF §4.17: the real cast portrait scored 0.75 through the Curator) — documented rather
than silently picked, same discipline, lower stakes.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

# The one backend import in this otherwise dependency-light file, and it earns itself: the scene
# vocabulary is the world model's input, and a second hand-maintained copy of it here would drift the
# moment the enum gained a value — leaving the eval passing a photo whose setting the pipeline had
# started rejecting. Safe to import because both consumers (`backend/seed.py`, `eval/run_eval.py`) put
# `backend/` on `sys.path` *before* importing this module.
from schemas.common import SceneSetting  # noqa: E402

SCENE_SETTINGS = frozenset(s.value for s in SceneSetting)

AESTHETIC_LOW = 0.35
AESTHETIC_HIGH = 0.40


@dataclass(frozen=True)
class Fixture:
    fixtureId: str
    label: str
    kind: str  # "cast" | "synthetic"
    stageHint: str  # the stage window this fixture's captured-at timestamp falls inside
    captureOffsetHours: float  # relative to the event's `now` anchor (matches dev_event's window)
    aesthetic: str  # "low" | "high" | "any"
    assertStage: bool  # whether stageId must equal stageHint (skipped when visual content is ambiguous)
    consent: str = "public"
    #: Set by `build_fixtures()` for cast fixtures; synthetic ones build their own bytes.
    imageBytes: Callable[[], bytes] | None = None
    castSlug: str | None = None


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _gradient_jpeg(seed: int) -> bytes:
    """A smooth gradient — visually near-empty, so the Curator's honest answer is a low score."""
    rng = random.Random(seed)
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


def _solid_jpeg(seed: int) -> bytes:
    rng = random.Random(seed)
    color = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    img = Image.new("RGB", (1200, 900), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _noise_jpeg(seed: int) -> bytes:
    rng = random.Random(seed)
    img = Image.new("RGB", (600, 450))
    pixels = img.load()
    for y in range(450):
        for x in range(600):
            pixels[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


#: Stage windows, matching `scripts/dev_event.py::build_stages` exactly: haldi [-4,-1),
#: sangeet [-1,+3), ceremony [+3,+7), all relative to the anchor `now`.
_STAGES = ("haldi", "sangeet", "ceremony")


def build_fixtures(cast_members: list[Any]) -> list[Fixture]:
    """`cast_members` is `eval/cast.py::ensure_cast()`'s return value.

    Every cast member appears twice — once during the sangeet (their portrait's natural setting,
    string-lights-and-a-dance-floor) and once re-tagged as captured during the ceremony, so the
    same visual content is graded under two different temporal priors. Stage attribution is not
    asserted on the second copy: a posed portrait carries little scene evidence either way, and
    asserting an exact posterior argmax on an ambiguous photo would be grading the fixture's luck,
    not the pipeline.
    """
    fixtures: list[Fixture] = []

    for member in cast_members:
        fixtures.append(
            Fixture(
                fixtureId=f"cast_{member.slug}_sangeet",
                label=f"{member.displayName} ({member.tier}) — portrait, tagged sangeet",
                kind="cast",
                stageHint="sangeet",
                captureOffsetHours=1.0,
                aesthetic="high",
                assertStage=True,
                imageBytes=member.photo.read_bytes,
                castSlug=member.slug,
            )
        )
        fixtures.append(
            Fixture(
                fixtureId=f"cast_{member.slug}_ceremony",
                label=f"{member.displayName} ({member.tier}) — same portrait, tagged ceremony",
                kind="cast",
                stageHint="ceremony",
                captureOffsetHours=5.0,
                aesthetic="high",
                assertStage=False,
                imageBytes=member.photo.read_bytes,
                castSlug=member.slug,
            )
        )

    # 9 gradients: 3 per stage, spread across each window so the temporal prior's ±30 min ramp
    # (spec 03 §5.1) is exercised well inside a window, not right at an edge.
    gradient_offsets = {
        "haldi": (-3.5, -2.5, -1.5),
        "sangeet": (-0.5, 1.0, 2.5),
        "ceremony": (3.5, 5.0, 6.5),
    }
    for stage, offsets in gradient_offsets.items():
        for i, offset in enumerate(offsets):
            seed = hash((stage, i)) & 0xFFFF
            fixtures.append(
                Fixture(
                    fixtureId=f"gradient_{stage}_{i}",
                    label=f"synthetic gradient, tagged {stage} t+{offset}h",
                    kind="synthetic",
                    stageHint=stage,
                    captureOffsetHours=offset,
                    aesthetic="low",
                    assertStage=True,
                    imageBytes=lambda s=seed: _gradient_jpeg(s),
                )
            )

    # 4 floor-testing edge cases, padding the set to 25 and covering two more "no evidence" shapes.
    for i, (kind_label, builder) in enumerate(
        [("solid", _solid_jpeg), ("solid", _solid_jpeg), ("noise", _noise_jpeg), ("noise", _noise_jpeg)]
    ):
        stage = _STAGES[i % len(_STAGES)]
        seed = 9000 + i
        fixtures.append(
            Fixture(
                fixtureId=f"{kind_label}_{i}",
                label=f"synthetic {kind_label} fill, tagged {stage}",
                kind="synthetic",
                stageHint=stage,
                captureOffsetHours={"haldi": -2.0, "sangeet": 0.5, "ceremony": 4.0}[stage],
                aesthetic="low",
                assertStage=True,
                imageBytes=lambda s=seed, b=builder: b(s),
            )
        )

    return fixtures


def evaluate(fixture: Fixture, doc: dict[str, Any], glossary: set[str]) -> list[Check]:
    """Grade one seeded media doc against its fixture's expectations. Never raises."""
    checks: list[Check] = []
    stages = doc.get("stages") or {}
    settled = bool(stages) and all(s in ("done", "failed", "failed_permanent") for s in stages.values())
    checks.append(
        Check(
            "reached_indexed",
            doc.get("status") == "indexed",
            f"status={doc.get('status')!r} stages={stages}" if not settled or doc.get("status") != "indexed" else "ok",
        )
    )

    curator = doc.get("curator") or {}
    aesthetic = float(curator.get("aestheticScore") or 0.0)
    if fixture.aesthetic == "low":
        ok = aesthetic < AESTHETIC_LOW
    elif fixture.aesthetic == "high":
        ok = aesthetic >= AESTHETIC_HIGH
    else:
        ok = True
    checks.append(Check("aesthetic_bucket", ok, f"aestheticScore={aesthetic:.2f} (expected {fixture.aesthetic})"))

    if fixture.assertStage:
        stage_id = curator.get("stageId")
        # `None` is the honest answer when the model returns no visual stage scores at all (spec
        # 03 §5.1 — fusion has nothing to fuse the temporal prior with, so it claims nothing rather
        # than guess). A synthetic fixture with no recognisable content legitimately lands either
        # on its tagged stage or on null; only a *different* stage is a real fusion regression.
        ok = stage_id in (None, fixture.stageHint)
        checks.append(
            Check("stage_attribution", ok, f"stageId={stage_id!r} expected {fixture.stageHint!r} or null")
        )

    guardian = doc.get("guardian") or {}
    verdict = guardian.get("verdict")
    checks.append(Check("guardian_ran", verdict is not None, f"guardian.verdict={verdict!r}"))
    checks.append(Check("not_blocked", verdict != "blocked", f"guardian.verdict={verdict!r}"))

    elements = curator.get("culturalElements") or []
    stray = [e for e in elements if e not in glossary]
    checks.append(Check("cultural_elements_within_glossary", not stray, f"stray={stray}" if stray else "ok"))

    # The world model's input vocabulary (spec 03 §5.1's `sceneSetting`). Graded as *membership only*,
    # deliberately not as an expected value per fixture: the synthetic gradients and solid fills have
    # no real setting, so asserting one would be grading the fixture's luck rather than the pipeline —
    # the same reasoning `assertStage` carries above. What must hold is that the coercion in
    # `workers/curate/app.py::_scene_setting` never lets a value outside the enum reach the document,
    # because every one of these becomes a Firestore map key on a coverage shard.
    setting = curator.get("sceneSetting")
    checks.append(
        Check(
            "scene_setting_in_vocabulary",
            setting in SCENE_SETTINGS,
            f"sceneSetting={setting!r} not in the closed vocabulary" if setting not in SCENE_SETTINGS else "ok",
        )
    )

    return checks
