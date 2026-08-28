"""The reel document and the two model contracts behind it (spec 06 §1).

Three rules carried over from `schemas/director.py`, for the same reasons:

**No free-form maps.** The response-schema dialect has no open-ended map, and a `dict[str, X]`
comes back silently empty rather than rejected. Every field the model fills is a scalar, an enum
or a list of those.

**No prose in `description=`.** The schema is billed on every request, roughly twice over (ADK
sends it as `response_schema` *and* as a JSON instruction). The rules live once in the agent
instruction; this file carries names and bounds.

**The schema is a parsing convenience, not the validation.** `directors/reel/critic.py::lint`
re-checks every shot against the candidate set that was actually selected, and
`directors/reel/edl.py` recomputes every geometric number the render depends on. A well-formed
`ReelPlan` naming a mediaId that is not in the manifest is still rejected.

One choice here is load-bearing enough to state: **the model chooses a Ken Burns *gesture*, never a
rectangle.** Spec 06 §3 asks for `kenBurns {from,to} anchored on face boxes`, and spec 06 §8's last
acceptance criterion is that no face crosses the frame edge during any move. Asking a language model
for four floats per shot and then linting them would make that criterion something we *test*; asking
it for `PUSH_IN` and deriving both rectangles from the face boxes in `edl.py` makes it true by
construction (the proof is in that file). The model still exercises every structural degree of
freedom spec 06 §2.3 lists — shot count, pacing curve, transition palette, caption voice, music
brief — it just does not do arithmetic it cannot check.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field

from .common import Visibility


class ReelPersona(str, Enum):
    """Spec 06 §1's personas, verbatim and closed.

    `main_character` carries a personId and `stage_recap` a stageId; both ride in the reel document
    rather than in the enum, so the persona stays a lens and the target stays data.
    """

    COUPLE = "couple"
    STAGE_RECAP = "stage_recap"
    GUEST_ENERGY = "guest_energy"
    MAIN_CHARACTER = "main_character"


class ReelStatus(str, Enum):
    """Spec 06 §1's lifecycle, plus `failed`.

    `failed` is not in the spec's list and is not a synonym for `unpublished`: unpublished means a
    reel that was live and was pulled (spec 06 §7's consent interlock), which the kiosk must treat as
    a retraction. A commission that never produced a file is a different thing, and collapsing the
    two would make an `ops/` alert look like a guest exercising a veto.
    """

    DIRECTING = "directing"
    COMPOSING = "composing"
    RENDERING = "rendering"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    UNPUBLISHED = "unpublished"
    FAILED = "failed"


class KenBurnsMove(str, Enum):
    """The gesture vocabulary. `HOLD` exists so a shot can simply be looked at."""

    PUSH_IN = "push_in"
    PULL_OUT = "pull_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    HOLD = "hold"


class Transition(str, Enum):
    """Spec 06 §2.3: "3 of ~15 xfade types, chosen to match energy".

    Every value is a real ffmpeg `xfade` transition name, so the EDL is directly renderable and a
    typo cannot reach the filtergraph. `CUT` is the one that is not an xfade — it is the absence of
    one, and a montage with no hard cuts in it reads as a screensaver.
    """

    CUT = "cut"
    FADE = "fade"
    FADEBLACK = "fadeblack"
    FADEWHITE = "fadewhite"
    DISSOLVE = "dissolve"
    SMOOTHLEFT = "smoothleft"
    SMOOTHRIGHT = "smoothright"
    SMOOTHUP = "smoothup"
    WIPELEFT = "wipeleft"
    WIPERIGHT = "wiperight"
    SLIDELEFT = "slideleft"
    SLIDERIGHT = "slideright"
    CIRCLEOPEN = "circleopen"
    CIRCLECLOSE = "circleclose"
    RADIAL = "radial"


class PacingCurve(str, Enum):
    """Spec 06 §2.3's pacing curves, verbatim. Applied deterministically in `edl.py`."""

    LINEAR_BUILD = "linear_build"
    PEAK_AND_SETTLE = "peak_and_settle"
    TWO_ACT = "two_act"


# ---------------------------------------------------------------- what the director returns


class ShotPlan(BaseModel):
    """One shot as the model proposes it. `durationBeats` is beats, not seconds — the shot's real
    length is only known after Lyria returns and librosa finds the grid (spec 06 §3 step 4)."""

    mediaId: str
    durationBeats: int = Field(default=4, ge=1, le=16)
    move: KenBurnsMove = KenBurnsMove.PUSH_IN
    transition: Transition = Transition.DISSOLVE
    captionLine: str | None = None
    #: Emphasis shots are quantized to downbeats rather than to any beat (spec 06 §3 step 4).
    emphasis: bool = False


class MusicBrief(BaseModel):
    """Spec 06 §3 step 2's `musicBrief`. Becomes the Lyria prompt in `directors/reel/music.py`."""

    style: str = ""
    tempoBpm: int = Field(default=100, ge=50, le=180)
    arc: str = ""
    instruments: list[str] = Field(default_factory=list)
    culturalRefs: list[str] = Field(default_factory=list)


class ReelPlan(BaseModel):
    """The DIRECT step's whole output: brief, structure, shots, music."""

    narrativeBrief: str = ""
    title: str = ""
    pacing: PacingCurve = PacingCurve.LINEAR_BUILD
    captionVoice: str = ""
    shots: list[ShotPlan] = Field(default_factory=list)
    music: MusicBrief = Field(default_factory=MusicBrief)


class CriticVerdict(str, Enum):
    PASS = "PASS"
    REVISE = "REVISE"


class Critique(BaseModel):
    """The CRITIC step's rubric scoring (spec 06 §2.4). Scores the *storyboard*, never the photos."""

    verdict: CriticVerdict = CriticVerdict.PASS
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    momentsNamed: int = 0
    arcIsFlat: bool = False
    personaHonored: bool = True
    #: What to fix, appended verbatim to the regeneration prompt. Short, concrete, imperative.
    issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- the document


class ShotDoc(BaseModel):
    """One rendered shot: the EDL row. Every number here is deterministic (`edl.py`)."""

    mediaId: str
    startSec: float
    endSec: float
    #: Normalised [0,1] source rectangles, both guaranteed to contain every face box.
    fromRect: list[float] = Field(default_factory=list)
    toRect: list[float] = Field(default_factory=list)
    transition: Transition = Transition.DISSOLVE
    transitionSec: float = 0.0
    captionLine: str | None = None
    onBeat: float | None = None


class ReelDoc(BaseModel):
    """`events/{eventId}/reels/{reelId}` (spec 06 §1).

    Two field names differ from spec 06 §1's sketch and the deviation is deliberate:

    - **`videoUri`, not `outputUri`.** The kiosk's `ReelSlot` shipped in B3-S6 reading `videoUri`,
      and a client that already exists is the more binding contract. `gcsUri` carries the `gs://`
      provenance the host archive and a future re-render need.
    - **`visibility`, not `audienceRing`.** `firestore.rules` and the publisher's `reel_query` both
      already filter reels on `visibility == 'public'` — the same derived field media carries, for
      the same reason (spec 04 §2: one vocabulary for exposure, so one grep answers "what is
      public"). The commissioned ring survives as `audienceRing` for the audit.
    """

    reelId: str
    persona: ReelPersona
    stageId: str | None = None
    personId: str | None = None  # main_character target

    status: ReelStatus = ReelStatus.DIRECTING
    audienceRing: int = 2
    #: Derived from `audienceRing` + a live re-validation of every manifest asset at publish time
    #: (spec 06 §3 step 6). Never set by the model, never set optimistically before the file exists.
    visibility: Visibility | None = None

    title: str = ""
    narrativeBrief: str = ""
    pacing: PacingCurve | None = None
    captionVoice: str = ""
    styleSeed: int = 0

    music: MusicBrief | None = None
    #: Lyria's own free-text description of the clip it produced — provenance, not a caption
    #: (discovered in the B1 risk probe; `scripts/risk_tests/lyria.py`).
    musicCaption: str | None = None
    musicUri: str | None = None
    tempoBpm: float | None = None
    beatCount: int = 0

    shots: list[ShotDoc] = Field(default_factory=list)
    assetManifest: list[str] = Field(default_factory=list)
    candidateCount: int = 0
    candidateSnapshotAt: dt.datetime | None = None

    critique: Critique | None = None
    #: How many times DIRECT ran. 2 means the critic sent it back once (spec 06 §2.4's ≤1 retry).
    directAttempts: int = 0
    lintIssues: list[str] = Field(default_factory=list)

    durationSec: float | None = None
    progress: int = 0  # 0-100, written by the render job so the client can show a real bar
    videoUri: str | None = None  # the API redirect the kiosk <video> loads
    gcsUri: str | None = None
    sizeBytes: int | None = None

    version: int = 1
    previousVersionId: str | None = None
    commissionedBy: str = "director"  # director | host | guest
    commissionReason: str = ""
    failureReason: str | None = None

    usage: dict[str, int] = Field(default_factory=dict)
    costUsd: float = 0.0

    createdAt: dt.datetime | None = None
    publishedAt: dt.datetime | None = None
    updatedAt: dt.datetime | None = None
